"""Color Mode Data section (usually empty for RGB)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mockupgen.binary.reader import BinaryReader
from mockupgen.binary.writer import BinaryWriter


@dataclass
class ColorModeData:
    """Section 2 of a PSD file.

    For RGB / Grayscale / CMYK this is almost always empty (length = 0).
    Only Indexed and Duotone modes store meaningful data here.
    """

    data: bytes = field(default_factory=bytes)

    @classmethod
    def read(cls, reader: BinaryReader) -> "ColorModeData":
        length = reader.read_u32()
        data = reader.read(length) if length else b""
        return cls(data=data)

    def write(self, writer: BinaryWriter) -> None:
        writer.write_u32(len(self.data))
        if self.data:
            writer.write(self.data)
