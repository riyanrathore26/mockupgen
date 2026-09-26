"""Placed Layer (PlLd) transform parsing — used by export/composite."""

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
    """

    uuid: str
    page: int
    total_pages: int
    anti_alias: int
    layer_type: int  # 0=unknown, 1=vector, 2=raster, 3=image stack
    corners: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        xs = [c[0] for c in self.corners]
        ys = [c[1] for c in self.corners]
        return (
            int(min(xs)),
            int(min(ys)),
            int(max(xs) + 0.5),
            int(max(ys) + 0.5),
        )


def parse_placed_layer(data: bytes) -> Optional[PlacedTransform]:
    """Parse a PlLd / plLd tagged-block payload.

    Layout (Adobe, version 3)::

        4s   'plcL'
        u32  version (3)
        ...  unique-id as printable ASCII (no explicit null required;
             the next field's leading zero bytes terminate the scan)
        u32  page
        u32  total_pages
        u32  anti_alias_policy
        u32  placed_layer_type
        8×f64 transform corners (TL, TR, BR, BL)
        u32  warp version + descriptor (ignored for now)
    """
    if len(data) < 8 + 16 + 64:
        return None
    if data[0:4] not in (b"plcL", b"plLd"):
        log.warning("PlLd: unexpected type %r", data[0:4])
        return None

    version = struct.unpack(">I", data[4:8])[0]
    if version not in (1, 2, 3):
        log.warning("PlLd: unsupported version %d", version)

    # Unique ID: ASCII from offset 8 until non-printable
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

    log.debug(
        "PlLd uuid=%s type=%d corners=%s",
        uuid,
        ptype,
        [(round(x, 1), round(y, 1)) for x, y in corners],
    )
    return PlacedTransform(
        uuid=uuid,
        page=page,
        total_pages=total,
        anti_alias=aa,
        layer_type=ptype,
        corners=corners,
    )


def get_placed_transform(layer: LayerRecord) -> Optional[PlacedTransform]:
    """Return the PlacedTransform for a smart-object layer, if present."""
    for key in (KEY_PLACED_LAYER, KEY_PLACED_LAYER_OLD):
        block = layer.get_tagged_block(key)
        if block is not None:
            return parse_placed_layer(block.data)
    return None
