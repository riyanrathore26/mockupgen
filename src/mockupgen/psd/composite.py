"""Phase 5 — composite / export (perspective warp + optional soft fabric detail).

Strategy (v3 — clean):
1. Decode the flattened Image Data as the base canvas.
2. Perspective-warp each replacement design onto its PlLd 4 corners.
3. Optionally multiply a *subtle* high-pass fabric detail from the
   original composite (strength ~0.25) so folds appear without blow-outs.
4. Alpha-composite the warped design on top of the base (opaque design
   fully covers the old artwork in that region).

No harsh luminance remapping — that caused the white streak artifacts.
"""

from __future__ import annotations

import io
import struct
from typing import Optional

from PIL import Image, ImageChops, ImageFilter

from mockupgen.compression.packbits import decode_packbits
from mockupgen.log import get_logger
from mockupgen.psd.document import PSDDocument
from mockupgen.psd.placed_layer import get_placed_transform

log = get_logger("composite")

# How strongly to apply fabric-fold detail (0 = flat clean, 1 = full).
# Keep low to avoid white blow-outs.
FABRIC_STRENGTH = 0.25


def decode_image_data(doc: PSDDocument) -> Image.Image:
    header = doc.header
    w, h = header.width, header.height
    ch = header.channels
    depth = header.depth
    if depth != 8:
        raise NotImplementedError(f"Only 8-bit depth supported for export, got {depth}")

    raw = doc.image_data.raw
    comp = doc.image_data.compression

    if comp == 0:
        planes = _split_raw_planes(raw, w, h, ch)
    elif comp == 1:
        planes = _split_rle_planes(raw, w, h, ch)
    else:
        raise NotImplementedError(
            f"Image-data compression {comp} not supported (0=raw, 1=RLE only)"
        )

    if ch >= 3:
        bands = [Image.frombytes("L", (w, h), planes[i]) for i in range(3)]
        return Image.merge("RGB", bands)
    if ch == 1:
        return Image.frombytes("L", (w, h), planes[0]).convert("RGB")
    raise NotImplementedError(f"Unsupported channel count {ch}")


def _split_raw_planes(raw: bytes, w: int, h: int, ch: int) -> list[bytes]:
    plane_size = w * h
    return [raw[c * plane_size : (c + 1) * plane_size] for c in range(ch)]


def _split_rle_planes(raw: bytes, w: int, h: int, ch: int) -> list[bytes]:
    n_rows = ch * h
    header_bytes = n_rows * 2
    if len(raw) < header_bytes:
        raise ValueError("RLE image data too short for row-length table")
    row_lengths = struct.unpack(f">{n_rows}H", raw[:header_bytes])
    pos = header_bytes
    planes: list[bytes] = []
    for c in range(ch):
        plane = bytearray()
        for y in range(h):
            rl = row_lengths[c * h + y]
            plane.extend(decode_packbits(raw[pos : pos + rl], w))
            pos += rl
        planes.append(bytes(plane))
    return planes


def _find_perspective_coeffs(
    src: list[tuple[float, float]],
    dst: list[tuple[float, float]],
) -> list[float]:
    matrix = []
    for (x, y), (u, v) in zip(src, dst):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    B = [c for p in dst for c in p]
    n = 8
    M = [matrix[i][:] + [B[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[pivot] = M[pivot], M[col]
        div = M[col][col]
        if abs(div) < 1e-12:
            raise ValueError("Degenerate perspective quad")
        M[col] = [v / div for v in M[col]]
        for row in range(n):
            if row == col:
                continue
            factor = M[row][col]
            M[row] = [a - factor * b for a, b in zip(M[row], M[col])]
    return [M[i][n] for i in range(n)]


def warp_to_quad(
    src: Image.Image,
    corners: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ],
    canvas_size: tuple[int, int],
) -> Image.Image:
    w, h = src.size
    src_corners = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    coeffs = _find_perspective_coeffs(list(corners), src_corners)
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    return src.transform(
        canvas_size,
        Image.Transform.PERSPECTIVE,
        coeffs,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )


def linked_file_to_image(file_data: bytes) -> Optional[Image.Image]:
    if not file_data:
        return None
    magic = file_data[:4]
    if magic == b"\x89PNG" or magic[:3] == b"\xff\xd8\xff" or magic[:4] == b"GIF8":
        return Image.open(io.BytesIO(file_data)).convert("RGBA")
    if magic == b"8BPS":
        try:
            return _decode_embedded_psd_image(file_data)
        except Exception as exc:
            log.warning("Could not decode embedded PSD/PSB: %s", exc)
            return None
    return None


def _decode_embedded_psd_image(data: bytes) -> Image.Image:
    if data[0:4] != b"8BPS":
        raise ValueError("Not 8BPS")
    version = struct.unpack(">H", data[4:6])[0]
    channels = struct.unpack(">H", data[12:14])[0]
    height = struct.unpack(">I", data[14:18])[0]
    width = struct.unpack(">I", data[18:22])[0]
    depth = struct.unpack(">H", data[22:24])[0]
    if depth != 8:
        raise NotImplementedError(f"embedded depth {depth}")
    pos = 26
    cmd_len = struct.unpack(">I", data[pos : pos + 4])[0]
    pos += 4 + cmd_len
    ir_len = struct.unpack(">I", data[pos : pos + 4])[0]
    pos += 4 + ir_len
    if version == 1:
        lm_len = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4 + lm_len
    else:
        lm_len = struct.unpack(">Q", data[pos : pos + 8])[0]
        pos += 8 + lm_len
    compression = struct.unpack(">H", data[pos : pos + 2])[0]
    pos += 2
    raw = data[pos:]
    if compression == 0:
        planes = _split_raw_planes(raw, width, height, channels)
    elif compression == 1:
        planes = _split_rle_planes(raw, width, height, channels)
    else:
        raise NotImplementedError(f"embedded compression {compression}")
    if channels >= 3:
        bands = [Image.frombytes("L", (width, height), planes[i]) for i in range(3)]
        img = Image.merge("RGB", bands).convert("RGBA")
        if channels >= 4:
            img.putalpha(Image.frombytes("L", (width, height), planes[3]))
        return img
    raise NotImplementedError(f"embedded channels {channels}")


def _fabric_detail_map(
    composite: Image.Image,
    corners: tuple,
    canvas_size: tuple[int, int],
) -> Image.Image:
    """Grayscale high-pass of the design region, centred on 128."""
    solid = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    footprint = warp_to_quad(solid, corners, canvas_size)
    alpha = footprint.split()[3]

    gray = composite.convert("L")
    blur = gray.filter(ImageFilter.GaussianBlur(radius=12))
    detail = ImageChops.subtract(gray, blur, scale=1.0, offset=128)

    neutral = Image.new("L", canvas_size, 128)
    return Image.composite(detail, neutral, alpha)


def _apply_soft_fabric(
    warped: Image.Image,
    detail: Image.Image,
    strength: float = FABRIC_STRENGTH,
) -> Image.Image:
    """Gently multiply fabric detail onto warped RGBA design."""
    if strength <= 0:
        return warped

    warped = warped.convert("RGBA")
    wr, wg, wb, wa = warped.split()

    neutral = Image.new("L", detail.size, 128)
    effective = Image.blend(neutral, detail, strength)

    def soft_mul(ch: Image.Image, d: Image.Image) -> Image.Image:
        m = ImageChops.multiply(ch, d)
        return m.point(lambda x: min(255, int(x * 255 / 128)))

    nr = soft_mul(wr, effective)
    ng = soft_mul(wg, effective)
    nb = soft_mul(wb, effective)
    return Image.merge("RGBA", (nr, ng, nb, wa))


def composite_document(
    doc: PSDDocument,
    *,
    overrides: Optional[dict[str, Image.Image]] = None,
    original_composite: Optional[Image.Image] = None,
    fabric_strength: float = FABRIC_STRENGTH,
) -> Image.Image:
    """Build an exported RGB image of the document."""
    overrides = overrides or {}
    base_rgb = decode_image_data(doc)
    canvas_size = (doc.width, doc.height)
    source = original_composite if original_composite is not None else base_rgb

    result = base_rgb.convert("RGBA")

    for layer in doc.layers():
        if not layer.is_smart_object:
            continue
        name = layer.display_name
        img = overrides.get(name)
        if img is None:
            continue

        transform = get_placed_transform(layer)
        if transform is None:
            log.debug("No PlLd for layer %r — skip", name)
            continue

        log.info(
            "Compositing smart object %r opacity=%d corners=%s fabric=%.2f",
            name,
            layer.opacity,
            [(round(x, 1), round(y, 1)) for x, y in transform.corners],
            fabric_strength,
        )

        warped = warp_to_quad(img, transform.corners, canvas_size)

        if fabric_strength > 0 and source is not None:
            detail = _fabric_detail_map(source, transform.corners, canvas_size)
            warped = _apply_soft_fabric(warped, detail, fabric_strength)

        if layer.opacity < 255:
            a = warped.split()[3].point(lambda p: p * layer.opacity // 255)
            warped.putalpha(a)

        result = Image.alpha_composite(result, warped)

    return result.convert("RGB")
