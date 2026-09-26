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

    ``corners`` are (x, y) in document pixel space, order:
    top-left, top-right, bottom-right, bottom-left.

    ``mesh_points`` is the 4×4 custom-envelope-warp control grid
    (16 points, row-major, document space) when present.
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

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        pts = self.mesh_points if self.mesh_points else self.corners
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


def _parse_mesh_points(data: bytes, start: int) -> Optional[list[tuple[float, float]]]:
    """Parse customEnvelopeWarp meshPoints ObAr from PlLd warp descriptor."""
    idx = data.find(b"ObAr", start)
    if idx < 0 or idx + 20 > len(data):
        return None
    pos = idx + 4
    try:
        pos += 4  # version (16)
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
            pos += 8  # UnFl + #Pxl
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
        log.debug("mesh parse failed: %s", exc)
        return None


def parse_placed_layer(data: bytes) -> Optional[PlacedTransform]:
    """Parse a PlLd / plLd tagged-block payload."""
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

    mesh = _parse_mesh_points(data, pos)

    log.debug(
        "PlLd uuid=%s type=%d corners=%s mesh=%s",
        uuid,
        ptype,
        [(round(x, 1), round(y, 1)) for x, y in corners],
        f"{len(mesh)} pts" if mesh else "none",
    )
    return PlacedTransform(
        uuid=uuid,
        page=page,
        total_pages=total,
        anti_alias=aa,
        layer_type=ptype,
        corners=corners,
        mesh_points=mesh,
    )


def get_placed_transform(layer: LayerRecord) -> Optional[PlacedTransform]:
    for key in (KEY_PLACED_LAYER, KEY_PLACED_LAYER_OLD):
        block = layer.get_tagged_block(key)
        if block is not None:
            return parse_placed_layer(block.data)
    return None
