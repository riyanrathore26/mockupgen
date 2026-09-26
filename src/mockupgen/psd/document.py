"""High-level PSD document — the entry point for opening a file."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from mockupgen.binary.reader import BinaryReader
from mockupgen.psd.header import FileHeader


class PSDDocument:
    """Represents an open PSD file.

    At this early stage we only parse the File Header.
    Later phases will add Color Mode, Image Resources,
    Layer & Mask Information, Smart Objects, and Image Data.
    """

    def __init__(self, header: FileHeader, raw: bytes):
        self.header = header
        self._raw = raw  # keep the full binary so later stages can continue parsing

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def open(cls, source: str | Path | bytes | BinaryIO) -> "PSDDocument":
        if isinstance(source, (str, Path)):
            data = Path(source).read_bytes()
        elif isinstance(source, bytes):
            data = source
        else:
            data = source.read()

        reader = BinaryReader(data)
        header = FileHeader.read(reader)

        return cls(header=header, raw=data)

    # ------------------------------------------------------------------
    # Convenience properties (will grow)
    # ------------------------------------------------------------------

    @property
    def width(self) -> int:
        return self.header.width

    @property
    def height(self) -> int:
        return self.header.height

    @property
    def depth(self) -> int:
        return self.header.depth

    @property
    def color_mode(self) -> int:
        return self.header.color_mode

    def __repr__(self) -> str:
        return f"<PSDDocument {self.header}>"
