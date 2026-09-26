"""PSD File Header (fixed 26 bytes)."""

from __future__ import annotations

from dataclasses import dataclass

from mockupgen.binary.reader import BinaryReader
from mockupgen.binary.writer import BinaryWriter

# Color mode constants (from the PSD spec)
COLOR_MODE_BITMAP = 0
COLOR_MODE_GRAYSCALE = 1
COLOR_MODE_INDEXED = 2
COLOR_MODE_RGB = 3
COLOR_MODE_CMYK = 4
COLOR_MODE_MULTICHANNEL = 7
COLOR_MODE_DUOTONE = 8
COLOR_MODE_LAB = 9

COLOR_MODE_NAMES = {
    0: "Bitmap",
    1: "Grayscale",
    2: "Indexed",
    3: "RGB",
    4: "CMYK",
    7: "Multichannel",
    8: "Duotone",
    9: "Lab",
}


@dataclass
class FileHeader:
    """The 26-byte PSD file header."""

    signature: bytes = b"8BPS"  # must be b'8BPS'
    version: int = 1            # 1 = PSD, 2 = PSB (we only support 1)
    channels: int = 3           # 1-56
    height: int = 0             # 1 .. 30_000
    width: int = 0              # 1 .. 30_000
    depth: int = 8              # 1, 8, 16, 32
    color_mode: int = 3         # see COLOR_MODE_* above

    # ------------------------------------------------------------------
    # Parsing / writing
    # ------------------------------------------------------------------

    @classmethod
    def read(cls, reader: BinaryReader) -> "FileHeader":
        signature = reader.read(4)
        if signature != b"8BPS":
            raise ValueError(f"Not a PSD file (signature={signature!r})")

        version = reader.read_u16()
        if version != 1:
            raise ValueError(
                f"Only PSD (version 1) is supported, got version={version} "
                "(PSB / Large Document Format is intentionally out of scope)"
            )

        reserved = reader.read(6)
        if reserved != b"\x00" * 6:
            # Spec says must be zero; we warn but continue
            pass

        channels = reader.read_u16()
        height = reader.read_u32()
        width = reader.read_u32()
        depth = reader.read_u16()
        color_mode = reader.read_u16()

        return cls(
            signature=signature,
            version=version,
            channels=channels,
            height=height,
            width=width,
            depth=depth,
            color_mode=color_mode,
        )

    def write(self, writer: BinaryWriter) -> None:
        writer.write(self.signature)
        writer.write_u16(self.version)
        writer.write(b"\x00" * 6)  # reserved
        writer.write_u16(self.channels)
        writer.write_u32(self.height)
        writer.write_u32(self.width)
        writer.write_u16(self.depth)
        writer.write_u16(self.color_mode)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def color_mode_name(self) -> str:
        return COLOR_MODE_NAMES.get(self.color_mode, f"Unknown({self.color_mode})")

    def __str__(self) -> str:
        return (
            f"PSD {self.width}x{self.height} "
            f"{self.depth}-bit {self.color_mode_name} "
            f"({self.channels} channels)"
        )
