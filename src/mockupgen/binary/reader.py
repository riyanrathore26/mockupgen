"""Low-level big-endian binary reader for PSD files."""

from __future__ import annotations

import struct
from typing import BinaryIO


class BinaryReader:
    """Sequential big-endian binary reader with position tracking."""

    def __init__(self, data: bytes | bytearray | memoryview | BinaryIO):
        if hasattr(data, "read"):
            self._data = memoryview(data.read())
        else:
            self._data = memoryview(data)
        self._pos = 0

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    @property
    def position(self) -> int:
        return self._pos

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    @property
    def length(self) -> int:
        return len(self._data)

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self._data):
            raise IndexError(f"seek out of range: {pos}")
        self._pos = pos

    def skip(self, n: int) -> None:
        self.seek(self._pos + n)

    def tell(self) -> int:
        return self._pos

    # ------------------------------------------------------------------
    # Primitive reads (big-endian)
    # ------------------------------------------------------------------

    def read(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            raise EOFError(f"need {n} bytes, only {self.remaining} left")
        chunk = self._data[self._pos : self._pos + n].tobytes()
        self._pos += n
        return chunk

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_u16(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def read_i16(self) -> int:
        return struct.unpack(">h", self.read(2))[0]

    def read_u32(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def read_i32(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def read_u64(self) -> int:
        return struct.unpack(">Q", self.read(8))[0]

    def read_f32(self) -> float:
        return struct.unpack(">f", self.read(4))[0]

    def read_f64(self) -> float:
        return struct.unpack(">d", self.read(8))[0]

    def read_fixed_string(self, n: int) -> str:
        """Read n bytes and decode as ASCII, stripping trailing nulls."""
        raw = self.read(n)
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    def read_pascal_string(self, pad_to: int = 2) -> str:
        """Read a Pascal string (1-byte length + chars) and align to pad_to."""
        length = self.read_u8()
        s = self.read(length).decode("ascii", errors="replace") if length else ""
        # total bytes so far = 1 + length; pad so that (1+length) % pad_to == 0
        total = 1 + length
        padding = (pad_to - (total % pad_to)) % pad_to
        if padding:
            self.skip(padding)
        return s

    def read_unicode_string(self) -> str:
        """Read a Unicode string: 4-byte length (num of UTF-16 code units) + data."""
        length = self.read_u32()  # number of 16-bit code units
        if length == 0:
            return ""
        raw = self.read(length * 2)
        # UTF-16 BE, strip trailing null if present
        return raw.decode("utf-16-be", errors="replace").rstrip("\x00")

    def align(self, boundary: int = 2) -> None:
        """Skip bytes so that current position is aligned to boundary."""
        remainder = self._pos % boundary
        if remainder:
            self.skip(boundary - remainder)
