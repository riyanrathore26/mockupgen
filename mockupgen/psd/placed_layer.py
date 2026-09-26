"""Placed Layer (PlLd) transform + custom envelope warp mesh parsing."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

from mockupgen.psd.layer_mask import LayerRecord, KEY_PLACED_LAYER, KEY_PLACED_LAYER_OLD
from mockupgen.log import get_logger

log = get_logger("placed_layer")


@dataclass
class PlacedTransform:
    """Placement of a smart object on the parent canvas.

    ``corners`` are (x, y) in document pixel space: TL, TR, BR, BL.

    ``mesh_points`` is the 4x4 custom-envelope-warp control grid (16 points,
    row-major). Coordinates may be in document space or local (smart-object
    native) space — use :meth:`document_mesh` to normalise.
    """

    uuid: str
    page: int
    total_pages: int
    anti_alias: int
    layer_type: int
    corners: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]
    mesh_points: Optional[list[tuple[float, float]]] = None
    mesh_bounds: Optional[tuple[float, float, float, float]] = None

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        pts = self.document_mesh() or list(self.corners)
        xs = [c[0] for c in pts]
        ys = [c[1] for c in pts]
        return (
            int(min(xs)),
            int(min(ys)),
            int(max(xs) + 0.5),
            int(max(ys) + 0.5),
        )

    @property
    def has_mesh_warp(self) -> bool:
        return bool(self.mesh_points) and len(self.mesh_points) == 16

    def mesh_is_local(
        self,
        canvas_size: tuple[int, int] | None = None,
        local_size: tuple[float, float] | None = None,
    ) -> bool:
        """True if mesh points look like local SO coordinates (near origin / inside SO size)."""
        if not self.mesh_points:
            return False
        xs = [p[0] for p in self.mesh_points]
        ys = [p[1] for p in self.mesh_points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max_x - min_x
        span_y = max_y - min_y

        # Strongest signal: mesh lives inside the smart-object native size
        if local_size is not None:
            lw, lh = float(local_size[0]), float(local_size[1])
            if lw > 0 and lh > 0:
                if (
                    min_x >= -80
                    and min_y >= -80
                    and max_x <= lw + 80
                    and max_y <= lh + 80
                ):
                    return True
                # Mesh is clearly smaller than the document canvas
                if canvas_size:
                    cw, ch = canvas_size
                    if span_x < cw * 0.55 and span_y < ch * 0.55 and min_x > -100 and min_y > -100:
                        return True

        cx = sum(c[0] for c in self.corners) / 4
        cy = sum(c[1] for c in self.corners) / 4
        mesh_cx = (min_x + max_x) / 2
        mesh_cy = (min_y + max_y) / 2
        dist = ((mesh_cx - cx) ** 2 + (mesh_cy - cy) ** 2) ** 0.5
        if dist > 80:
            return True
        if canvas_size:
            cw, ch = canvas_size
            if min_x > -80 and min_y > -80 and max_x < cw * 0.65 and max_y < ch * 0.65:
                if abs(mesh_cx - cx) > 50 or abs(mesh_cy - cy) > 50:
                    return True
        return False

    def document_mesh(
        self,
        canvas_size: tuple[int, int] | None = None,
        local_size: tuple[float, float] | None = None,
    ) -> Optional[list[tuple[float, float]]]:
        """Return mesh points in document space.

        If the stored mesh is local (SO-native), maps each point through the
        bilinear map defined by ``corners``.
        """
        if not self.mesh_points or len(self.mesh_points) != 16:
            return self.mesh_points

        if not self.mesh_is_local(canvas_size, local_size=local_size):
            return self.mesh_points

        if self.mesh_bounds:
            left, top, right, bottom = self.mesh_bounds
            bw = right - left if right != left else 1.0
            bh = bottom - top if bottom != top else 1.0
        elif local_size:
            left = top = 0.0
            bw, bh = float(local_size[0]), float(local_size[1])
        else:
            xs = [p[0] for p in self.mesh_points]
            ys = [p[1] for p in self.mesh_points]
            left, top = min(xs), min(ys)
            bw = max(xs) - left or 1.0
            bh = max(ys) - top or 1.0

        TL, TR, BR, BL = self.corners

        def bilinear(u: float, v: float) -> tuple[float, float]:
            top_x = TL[0] * (1 - u) + TR[0] * u
            top_y = TL[1] * (1 - u) + TR[1] * u
            bot_x = BL[0] * (1 - u) + BR[0] * u
            bot_y = BL[1] * (1 - u) + BR[1] * u
            return (top_x * (1 - v) + bot_x * v, top_y * (1 - v) + bot_y * v)

        doc: list[tuple[float, float]] = []
        for lx, ly in self.mesh_points:
            u = (lx - left) / bw
            v = (ly - top) / bh
            doc.append(bilinear(u, v))

        log.debug(
            "Mapped local mesh -> document (bounds %.0fx%.0f, corner TL~%s)",
            bw,
            bh,
            (round(doc[0][0]), round(doc[0][1])),
        )
        return doc


def _parse_mesh_bounds(data: bytes, start: int) -> Optional[tuple[float, float, float, float]]:
    idx = data.find(b"classFloatRect", start)
    if idx < 0:
        return None
    try:
        pos = idx + len(b"classFloatRect")
        count = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        vals: dict[str, float] = {}
        for _ in range(count):
            klen = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4
            if klen == 0:
                key = data[pos : pos + 4].decode("ascii", errors="replace")
                pos += 4
            else:
                key = data[pos : pos + klen].decode("ascii", errors="replace")
                pos += klen
            typ = data[pos : pos + 4]
            pos += 4
            if typ != b"doub":
                break
            val = struct.unpack(">d", data[pos : pos + 8])[0]
            pos += 8
            vals[key.strip()] = val
        top = vals.get("Top") or vals.get("Top ") or 0.0
        left = vals.get("Left") or vals.get("Left ") or 0.0
        bottom = vals.get("Btom") or vals.get("Bottom") or 0.0
        right = vals.get("Rght") or vals.get("Right") or 0.0
        if bottom > top or right > left:
            return (left, top, right, bottom)
    except Exception as exc:
        log.debug("bounds parse failed: %s", exc)
    return None


def _parse_mesh_points(data: bytes, start: int) -> Optional[list[tuple[float, float]]]:
    idx = data.find(b"ObAr", start)
    if idx < 0 or idx + 20 > len(data):
        return None
    pos = idx + 4
    try:
        pos += 4
        ulen = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4 + ulen * 2
        cid_len = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        if cid_len == 0:
            pos += 4
        else:
            pos += cid_len
        n_fields = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        coords: dict[str, list[float]] = {}
        for _ in range(n_fields):
            klen = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4
            if klen == 0:
                key = data[pos : pos + 4].decode("ascii", errors="replace")
                pos += 4
            else:
                key = data[pos : pos + klen].decode("ascii", errors="replace")
                pos += klen
            pos += 8
            count = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4
            vals = list(struct.unpack(f">{count}d", data[pos : pos + 8 * count]))
            pos += 8 * count
            coords[key] = vals
        xs = coords.get("Hrzn") or coords.get("hrzn")
        ys = coords.get("Vrtc") or coords.get("vrtc")
        if not xs or not ys or len(xs) != len(ys):
            return None
        return list(zip(xs, ys))
    except Exception as exc:
        log.debug("mesh parse failed: %s", exp)
        return None


def parse_placed_layer(data: bytes) -> Optional[PlacedTransform]:
    if len(data) < 8 + 16 + 64:
        return None
    if data[0:4] not in (b"plcL", b"plLd"):
        log.warning("PlLd: unexpected type %r", data[0:4])
        return None

    version = struct.unpack(">I", data[4:8])[0]
    if version not in (1, 2, 3):
        log.warning("PlLd: unsupported version %d", version)

    end = 8
    while end < len(data) and 32 <= data[end] < 127:
        end += 1
    uuid = data[8:end].decode("ascii", errors="replace")

    pos = end
    if pos + 16 + 64 > len(data):
        return None

    page, total, aa, ptype = struct.unpack(">4I", data[pos : pos + 16])
    pos += 16
    doubles = struct.unpack(">8d", data[pos : pos + 64])
    corners = (
        (doubles[0], doubles[1]),
        (doubles[2], doubles[3]),
        (doubles[4], doubles[5]),
        (doubles[6], doubles[7]),
    )
    pos += 64

    bounds = _parse_mesh_bounds(data, pos)
    mesh = _parse_mesh_points(data, pos)

    log.debug(
        "PlLd uuid=%s type=%d corners=%s mesh=%s bounds=%s",
        uuid,
        ptype,
        [(round(x, 1), round(y, 1)) for x, y in corners],
        f"{len(mesh)} pts" if mesh else "none",
        bounds,
    )
    return PlacedTransform(
        uuid=uuid,
        page=page,
        total_pages=total,
        anti_alias=aa,
        layer_type=ptype,
        corners=corners,
        mesh_points=mesh,
        mesh_bounds=bounds,
    )


def get_placed_transform(layer: LayerRecord) -> Optional[PlacedTransform]:
    for key in (KEY_PLACED_LAYER, KEY_PLACED_LAYER_OLD):
        block = layer.get_tagged_block(key)
        if block is not None:
            return parse_placed_layer(block.data)
    return None
