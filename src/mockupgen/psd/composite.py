"""Phase 5 — composite / export with perspective + bezier mesh warp."""

from __future__ import annotations

import io
import struct
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageChops

from mockupgen.compression.packbits import decode_packbits
from mockupgen.log import get_logger
from mockupgen.psd.document import PSDDocument
from mockupgen.psd.placed_layer import get_placed_transform

log = get_logger("composite")

MESH_GRID = 16


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
    corners: tuple,
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


def _bernstein3(i: int, t: float) -> float:
    return [1, 3, 3, 1][i] * (t ** i) * ((1 - t) ** (3 - i))


def _bezier_surface(
    u: float, v: float, pts: list[tuple[float, float]]
) -> tuple[float, float]:
    x = y = 0.0
    for j in range(4):
        for i in range(4):
            b = _bernstein3(i, u) * _bernstein3(j, v)
            px, py = pts[j * 4 + i]
            x += b * px
            y += b * py
    return x, y


def warp_to_mesh(
    src: Image.Image,
    mesh: list[tuple[float, float]],
    canvas_size: tuple[int, int],
    grid: int = MESH_GRID,
) -> Image.Image:
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    cw, ch = canvas_size
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    sw, sh = src.size

    grid_pts: list[list[tuple[float, float]]] = []
    for j in range(grid + 1):
        v = j / grid
        row = [_bezier_surface(i / grid, v, mesh) for i in range(grid + 1)]
        grid_pts.append(row)

    for j in range(grid):
        for i in range(grid):
            u0, u1 = i / grid, (i + 1) / grid
            v0, v1 = j / grid, (j + 1) / grid
            sx0, sx1 = u0 * sw, u1 * sw
            sy0, sy1 = v0 * sh, v1 * sh
            cell = src.crop(
                (
                    int(sx0),
                    int(sy0),
                    max(int(sx1), int(sx0) + 1),
                    max(int(sy1), int(sy0) + 1),
                )
            )
            if cell.size[0] < 1 or cell.size[1] < 1:
                continue
            cw_cell, ch_cell = cell.size
            src_corners = [
                (0.0, 0.0),
                (float(cw_cell), 0.0),
                (float(cw_cell), float(ch_cell)),
                (0.0, float(ch_cell)),
            ]
            dst_corners = [
                grid_pts[j][i],
                grid_pts[j][i + 1],
                grid_pts[j + 1][i + 1],
                grid_pts[j + 1][i],
            ]
            xs = [p[0] for p in dst_corners]
            ys = [p[1] for p in dst_corners]
            bx0 = max(0, int(min(xs)) - 1)
            by0 = max(0, int(min(ys)) - 1)
            bx1 = min(cw, int(max(xs)) + 2)
            by1 = min(ch, int(max(ys)) + 2)
            if bx1 <= bx0 or by1 <= by0:
                continue
            local_dst = [(x - bx0, y - by0) for x, y in dst_corners]
            try:
                local_coeffs = _find_perspective_coeffs(list(local_dst), src_corners)
            except ValueError:
                continue
            piece = cell.transform(
                (bx1 - bx0, by1 - by0),
                Image.Transform.PERSPECTIVE,
                local_coeffs,
                resample=Image.Resampling.BILINEAR,
                fillcolor=(0, 0, 0, 0),
            )
            out.paste(piece, (bx0, by0), piece)
    return out


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


def _apply_fabric_shading(
    warped: Image.Image,
    shirt_source: Image.Image,
    strength: float,
) -> Image.Image:
    """Multiply design by soft fabric fold map from the original composite."""
    strength = max(0.0, min(1.0, strength))
    if strength < 1e-6:
        return warped

    w, h = warped.size
    gray = shirt_source.convert("L").resize((w, h), Image.Resampling.BILINEAR)
    blur = gray.filter(ImageFilter.GaussianBlur(radius=18))
    detail = ImageChops.subtract(gray, blur, scale=1.0, offset=128)

    def map_factor(d: int) -> int:
        f = 1.0 + strength * (d - 128) / 128.0
        f = max(0.7, min(1.25, f))
        return int(round(f * 128))

    factor_l = detail.point(map_factor)
    r, g, b, a = warped.split()
    factor_rgb = Image.merge("RGB", (factor_l, factor_l, factor_l))
    rgb = Image.merge("RGB", (r, g, b))
    shaded = ImageChops.multiply(rgb, factor_rgb)
    shaded = shaded.point(lambda px: min(255, int(px * 255 / 128)))
    shaded = shaded.convert("RGBA")
    shaded.putalpha(a)
    return shaded


def composite_document(
    doc: PSDDocument,
    *,
    overrides: Optional[dict[str, Image.Image]] = None,
    original_composite: Optional[Image.Image] = None,
    fabric_strength: float = 0.35,
) -> Image.Image:
    """Build an exported RGB image of the document."""
    overrides = overrides or {}
    base_rgb = decode_image_data(doc)
    canvas_size = (doc.width, doc.height)

    if original_composite is not None:
        result = original_composite.convert("RGBA")
    else:
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
            "Compositing smart object %r opacity=%d mesh=%s",
            name,
            layer.opacity,
            "yes" if transform.has_mesh_warp else "no",
        )

        if transform.has_mesh_warp:
            mesh = transform.document_mesh(canvas_size)
            warped = warp_to_mesh(img, mesh, canvas_size)  # type: ignore
        else:
            warped = warp_to_quad(img, transform.corners, canvas_size)

        if fabric_strength > 0 and original_composite is not None:
            warped = _apply_fabric_shading(
                warped, original_composite, fabric_strength
            )

        if layer.opacity < 255:
            a = warped.split()[3].point(lambda p: p * layer.opacity // 255)
            warped.putalpha(a)

        result = Image.alpha_composite(result, warped)

    return result.convert("RGB")
