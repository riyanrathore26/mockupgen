"""Composite / export: decode image data, warp smart objects, alpha-composite."""

from __future__ import annotations

import io
import struct
from typing import Optional

from PIL import Image, ImageChops, ImageFilter

from mockupgen.log import get_logger
from mockupgen.psd.document import PSDDocument
from mockupgen.psd.placed_layer import get_placed_transform
from mockupgen.compression.packbits import decode_packbits

log = get_logger("composite")

MESH_GRID = 16


def decode_image_data(doc: PSDDocument) -> Image.Image:
    """Decode the flattened composite image (section 5) to RGB."""
    header = doc.header
    w, h = header.width, header.height
    channels = header.channels
    depth = header.depth
    if depth != 8:
        raise NotImplementedError(f"Only 8-bit depth supported, got {depth}")

    data = doc.image_data.raw
    compression = doc.image_data.compression

    if compression == 0:
        raw = data
        plane_size = w * h
        planes = [raw[i * plane_size : (i + 1) * plane_size] for i in range(min(channels, 3))]
    elif compression == 1:
        planes = _decode_rle_planes(data, w, h, channels)
    else:
        raise NotImplementedError(f"compression {compression}")

    if len(planes) >= 3:
        img = Image.merge("RGB", [Image.frombytes("L", (w, h), p) for p in planes[:3]])
    elif len(planes) == 1:
        img = Image.frombytes("L", (w, h), planes[0]).convert("RGB")
    else:
        img = Image.new("RGB", (w, h), (128, 128, 128))
    return img


def _decode_rle_planes(data: bytes, w: int, h: int, channels: int) -> list[bytes]:
    n = channels * h
    if len(data) < n * 2:
        raise ValueError("RLE row-length table truncated")
    lengths = struct.unpack(">" + "H" * n, data[: n * 2])
    pos = n * 2
    rows: list[bytes] = []
    for length in lengths:
        chunk = data[pos : pos + length]
        pos += length
        rows.append(decode_packbits(chunk, w))
    planes = []
    for c in range(channels):
        plane = b"".join(rows[c * h : (c + 1) * h])
        planes.append(plane)
    return planes


def _find_perspective_coeffs(dst, src):
    import numpy as np

    matrix = []
    for (x, y), (u, v) in zip(dst, src):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(matrix, dtype=float)
    B = np.array([u for (u, v) in src for u in (u, v)], dtype=float)
    try:
        res = np.linalg.solve(A, B)
    except np.linalg.LinAlgError:
        res = np.linalg.lstsq(A, B, rcond=None)[0]
    return tuple(res.tolist())


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
            log.warning("Could not decode embedded PSD/PSB: %s", exp)
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
        plane_size = width * height
        planes = [raw[i * plane_size : (i + 1) * plane_size] for i in range(min(channels, 4))]
    elif compression == 1:
        planes = _decode_rle_planes(raw, width, height, channels)
    else:
        raise NotImplementedError(f"embedded compression {compression}")
    if channels >= 3:
        rgb = Image.merge("RGB", [Image.frombytes("L", (width, height), p) for p in planes[:3]])
        if channels >= 4 and len(planes) > 3:
            a = Image.frombytes("L", (width, height), planes[3])
            rgb = rgb.convert("RGBA")
            rgb.putalpha(a)
            return rgb
        return rgb.convert("RGBA")
    return Image.frombytes("L", (width, height), planes[0]).convert("RGBA")


def _apply_fabric_shading(
    warped: Image.Image,
    original_composite: Image.Image,
    strength: float = 0.35,
) -> Image.Image:
    """Soft fabric folds from original composite high-pass."""
    if strength <= 0:
        return warped
    shirt_source = original_composite
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
    original_linked: Optional[dict[str, bytes]] = None,
) -> Image.Image:
    """Build an exported RGB image of the document.

    - Warps the new design with PlLd mesh / corners.
    - Erases the original design from the base (punch-hole) so nothing double-prints.
    - Multiplies the new design alpha by the original SO alpha (inherits author clipping).
    - Uses fabric high-pass both for shading and to further hide the design in deep folds.
    """
    overrides = overrides or {}
    original_linked = original_linked or {}
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

        has_mesh = transform.has_mesh_warp
        log.info(
            "Compositing smart object %r opacity=%d mesh=%s",
            name,
            layer.opacity,
            "yes" if has_mesh else "no",
        )

        local_size = (float(img.width), float(img.height))

        def _warp(src: Image.Image, loc_size: tuple[float, float]) -> Image.Image:
            if has_mesh:
                mesh = transform.document_mesh(canvas_size, local_size=loc_size)
                return warp_to_mesh(src, mesh, canvas_size)  # type: ignore
            return warp_to_quad(src, transform.corners, canvas_size)

        warped = _warp(img, local_size)

        # --- 1. Punch a hole: remove the old design from the base ---
        orig_data = original_linked.get(name)
        orig_warped = None
        if orig_data is not None:
            try:
                orig_img = linked_file_to_image(orig_data)
                if orig_img is not None:
                    if orig_img.mode != "RGBA":
                        orig_img = orig_img.convert("RGBA")
                    orig_warped = _warp(
                        orig_img, (float(orig_img.width), float(orig_img.height))
                    )
                    _, _, _, oa = orig_warped.split()
                    oa = oa.filter(ImageFilter.GaussianBlur(radius=1.2))
                    if original_composite is not None:
                        shirt = original_composite.convert("RGBA")
                        shirt_blur = shirt.filter(ImageFilter.GaussianBlur(radius=8))
                        result = Image.composite(shirt_blur, result, oa)
                    log.debug("Punched hole for original design of %r", name)
            except Exception as exc:
                log.debug("Could not punch hole for %r: %s", name, exp)
                orig_warped = None

        # --- 2. Clip new design to original SO visibility ---
        if orig_warped is not None:
            try:
                _, _, _, new_a = warped.split()
                _, _, _, orig_a = orig_warped.split()
                orig_a = orig_a.filter(ImageFilter.GaussianBlur(radius=0.6))
                combined_a = ImageChops.multiply(new_a, orig_a)
                warped.putalpha(combined_a)
                log.debug("Applied original SO alpha mask for %r", name)
            except Exception as exc:
                log.debug("Could not apply original mask for %r: %s", name, exp)

        # --- 3. Fabric shading + fold occlusion (dark folds hide design) ---
        if fabric_strength > 0 and original_composite is not None:
            warped = _apply_fabric_shading(
                warped, original_composite, fabric_strength
            )
            try:
                w, h = warped.size
                gray = original_composite.convert("L").resize((w, h), Image.Resampling.BILINEAR)
                blur = gray.filter(ImageFilter.GaussianBlur(radius=14))
                detail = ImageChops.subtract(gray, blur, scale=1.0, offset=128)
                def fold_alpha(d: int) -> int:
                    if d >= 128:
                        return 255
                    return max(100, int(140 + (d / 128.0) * 115))
                fold_mod = detail.point(fold_alpha)
                _, _, _, a = warped.split()
                a = ImageChops.multiply(a, fold_mod)
                warped.putalpha(a)
            except Exception as exp:
                log.debug("Fold occlusion failed for %r: %s", name, exp)

        if layer.opacity < 255:
            a = warped.split()[3].point(lambda p: p * layer.opacity // 255)
            warped.putalpha(a)

        result = Image.alpha_composite(result, warped)

    return result.convert("RGB")
