"""Layer and Mask Information section — placeholder for Phase 2.

This is the most complex section. It will eventually hold:
- Layer records (name, bounds, channels, blend mode, opacity, flags)
- Channel image data
- Global layer mask
- Tagged blocks (including Linked Layers / Smart Object data)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mockupgen.binary.reader import BinaryReader


@dataclass
class LayerAndMaskInfo:
    """Section 4 of a PSD file.

    For now we only capture the raw bytes so we can skip / rewrite later.
    Full parsing of layers and smart objects comes in the next phases.
    """

    raw: bytes = field(default_factory=bytes)

    @classmethod
    def read(cls, reader: BinaryReader) -> "LayerAndMaskInfo":
        # PSD (version 1) uses a 4-byte length
        length = reader.read_u32()
        raw = reader.read(length) if length else b""
        return cls(raw=raw)
