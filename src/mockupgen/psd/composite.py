"""Phase 5 — composite / export (warps + basic blend).

Strategy (pragmatic v1):
1. Decode the document's flattened Image Data as the base canvas.
2. For each smart-object layer that has replaceable raster content
   (PNG / JPEG linked file, or an in-memory replacement), perspective-
   warp it onto the 4 corners from PlLd and alpha-composite with the
   layer opacity.
3. Full mesh warps and advanced blend modes are future work; normal
   blend + perspective is enough for typical mockup export.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Optional

from PIL import Image

from mockupgen.compression.packbits import decode_packbits
from mockupgen.log import get_logger
from mockupgen.psd.document import PSDDocument
from mockupgen.psd.placed_layer import get_placed_transform

log = get_logger("composite")


# ---------------------------------------------------------------------------
# Flattened composite decode
# ---------------------------------------------------------------------------


def decode_image_data(doc: PSDDocument) -> Image.Image:
    """Decode Section 5 (flattened composite) into a PIL RGB/RGBA image."""
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

    if ch >= 4:
        bands = [Image.frombytes("L", (w, h), planes[i]) for i in range(4)]
        # PSD order is R,G,B,A or sometimes A first depending on mode;
        # for RGB mode channels 0,1,2 = R,G,B and there is no alpha in composite.
        return Image.merge("RGB", bands[:3])
    if ch == 3:
        bands = [Image.frombytes("L", (w, h), planes[i]) for i in range(3)]
        return Image.merge("RGB", bands)
    if ch == 1:
        return Image.frombytes("L", (w, h), planes[0]).convert("RGB")
    raise NotImplementedError(f"Unsupported channel count {ch}")


def _split_raw_planes(raw: bytes, w: int, h: int, ch: int) -> list[bytes]:
    plane_size = w * h
    planes = []
    for c in range(ch):
        start = c * plane_size
        planes.append(raw[start : start + plane_size])
    return planes


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
            row = raw[pos : pos + rl]
            pos += rl
            plane.extend(decode_packbits(row, w))
        planes.append(bytes(plane))
    return planes


# ---------------------------------------------------------------------------
# Perspective warp helpers
# ---------------------------------------------------------------------------


def _find_perspective_coeffs(
    src: list[tuple[float, float]],
    dst: list[tuple[float, float]],
) -> list[float]:
    """Compute 8 perspective coefficients mapping src quad → dst quad.

    Solves for coeffs such that Pillow ``Image.Transform.PERSPECTIVE``
    maps source coordinates to destination coordinates.
    """
    # Build the 8x8 system from the 4 point pairs
    matrix = []
    for (x, y), (u, v) in zip(src, dst):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])

    A = matrix
    B = [c for p in dst for c in p]

    # Gaussian elimination (no numpy dependency)
    n = 8
    M = [A[i][:] + [B[i]] for i in range(n)]
    for col in range(n):
        # pivot
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
    """Perspective-warp ``src`` so its corners land on ``corners`` of a transparent canvas."""
    w, h = src.size
    # Source corners: TL, TR, BR, BL
    src_corners = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    dst_corners = list(corners)

    # Pillow PERSPECTIVE maps output → input, so invert: dst is canvas, src is image
    coeffs = _find_perspective_coeffs(dst_corners, src_corners)

    if src.mode != "RGBA":
        src = src.convert("RGBA")

    warped = src.transform(
        canvas_size,
        Image.Transform.PERSPECTIVE,
        coeffs,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    return warped


# ---------------------------------------------------------------------------
# Linked-file → PIL
# ---------------------------------------------------------------------------


def linked_file_to_image(file_data: bytes) -> Optional[Image.Image]:
    """Open linked-file bytes as a PIL image when possible (PNG / JPEG / GIF)."""
    if not file_data:
        return None
    magic = file_data[:4]
    if magic == b"\x89PNG" or magic[:3] == b"\xff\xd8\xff" or magic[:4] == b"GIF8":
        return Image.open(io.BytesIO(file_data)).convert("RGBA")
    if magic == b"8BPS":
        # Embedded PSD/PSB — try flattened composite via our own reader
        try:
            return _decode_embedded_psd_image(file_data)
        except Exception as exc:
            log.warning("Could not decode embedded PSD/PSB: %s", exc)
            return None
    log.debug("Unknown linked-file magic %r — skip", magic)
    return None


def _decode_embedded_psd_image(data: bytes) -> Image.Image:
    """Minimal decode of an embedded PSD *or* PSB flattened image."""
    # Support version 1 (PSD) and 2 (PSB) for embedded smart objects only
    if data[0:4] != b"8BPS":
        raise ValueError("Not 8BPS")
    version = struct.unpack(">H", data[4:6])[0]
    channels = struct.unpack(">H", data[12:14])[0]
    height = struct.unpack(">I", data[14:18])[0]
    width = struct.unpack(">I", data[18:22])[0]
    depth = struct.unpack(">H", data[22:24])[0]
    color_mode = struct.unpack(">H", data[24:26])[0]
    if depth != 8:
        raise NotImplementedError(f"embedded depth {depth}")

    pos = 26
    # Color mode data
    cmd_len = struct.unpack(">I", data[pos : pos + 4])[0]
    pos += 4 + cmd_len
    # Image resources
    ir_len = struct.unpack(">I", data[pos : pos + 4])[0]
    pos += 4 + ir_len
    # Layer and mask
    if version == 1:
        lm_len = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4 + lm_len
    else:
        lm_len = struct.unpack(">Q", data[pos : pos + 8])[0]
        pos += 8 + lm_len
    # Image data
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
        img = Image.merge("RGB", bands)
        if channels >= 4:
            alpha = Image.frombytes("L", (width, height), planes[3])
            img = img.convert("RGBA")
            img.putalpha(alpha)
        else:
            img = img.convert("RGBA")
        return img
    raise NotImplementedError(f"embedded channels {channels}")


# ---------------------------------------------------------------------------
# High-level composite
# ---------------------------------------------------------------------------


def composite_document(
    doc: PSDDocument,
    *,
    overrides: Optional[dict[str, Image.Image]] = None,
) -> Image.Image:
    """Build an exported RGB image of the document.

    ``overrides`` maps smart-object layer display names to replacement
    PIL images (already loaded). When provided they are warped using
    that layer's PlLd transform and painted over the flattened base.
    """
    overrides = overrides or {}
    base = decode_image_data(doc).convert("RGBA")
    canvas_size = (doc.width, doc.height)

    for layer in doc.layers():
        if not layer.is_smart_object:
            continue
        name = layer.display_name
        transform = get_placed_transform(layer)
        if transform is None:
            log.debug("No PlLd for layer %r — skip", name)
            continue

        img = overrides.get(name)
        if img is None:
            continue  # only paint layers we have explicit content for

        log.info(
            "Compositing smart object %r opacity=%d corners=%s",
            name,
            layer.opacity,
            [(round(x, 1), round(y, 1)) for x, y in transform.corners],
        )
        warped = warp_to_quad(img, transform.corners, canvas_size)

        # Apply layer opacity
        if layer.opacity < 255:
            alpha = warped.split()[3].point(lambda a: a * layer.opacity // 255)
            warped.putalpha(alpha)

        # Normal blend only (Phase 5 v1)
        base = Image.alpha_composite(base, warped)

    return base.convert("RGB")
