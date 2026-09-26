"""Image Data section (flattened composite) — placeholder."""

from __future__ import annotations

from dataclasses import dataclass, field

from mockupgen.binary.reader import BinaryReader


@dataclass
class ImageData:
    """Section 5 of a PSD file — the flattened preview image.

    Compression:
      0 = raw
      1 = PackBits RLE
      2 = ZIP without prediction
      3 = ZIP with prediction
    """

    compression: int = 0
    raw: bytes = field(default_factory=bytes)

    @classmethod
    def read(cls, reader: BinaryReader) -> "ImageData":
        compression = reader.read_u16()
        raw = reader.read(reader.remaining)  # rest of the file
        return cls(compression=compression, raw=raw)
