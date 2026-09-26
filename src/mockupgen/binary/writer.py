"""Low-level big-endian binary writer for PSD files."""

from __future__ import annotations

import struct
from io import BytesIO


class BinaryWriter:
    """Sequential big-endian binary writer."""

    def __init__(self) -> None:
        self._buf = BytesIO()

    def getvalue(self) -> bytes:
        return self._buf.getvalue()

    def tell(self) -> int:
        return self._buf.tell()

    # ------------------------------------------------------------------
    # Primitive writes (big-endian)
    # ------------------------------------------------------------------

    def write(self, data: bytes | bytearray | memoryview) -> None:
        self._buf.write(data)

    def write_u8(self, v: int) -> None:
        self._buf.write(struct.pack(">B", v))

    def write_u16(self, v: int) -> None:
        self._buf.write(struct.pack(">H", v))

    def write_i16(self, v: int) -> None:
        self._buf.write(struct.pack(">h", v))

    def write_u32(self, v: int) -> None:
        self._buf.write(struct.pack(">I", v))

    def write_i32(self, v: int) -> None:
        self._buf.write(struct.pack(">i", v))

    def write_u64(self, v: int) -> None:
        self._buf.write(struct.pack(">Q", v))

    def write_f64(self, v: float) -> None:
        self._buf.write(struct.pack(">d", v))

    def write_pascal_string(self, s: str, pad_to: int = 2) -> None:
        encoded = s.encode("ascii", errors="replace")[:255]
        self.write_u8(len(encoded))
        self.write(encoded)
        total = 1 + len(encoded)
        padding = (pad_to - (total % pad_to)) % pad_to
        if padding:
            self.write(b"\x00" * padding)

    def write_unicode_string(self, s: str) -> None:
        # Photoshop expects a trailing null code unit
        encoded = (s + "\x00").encode("utf-16-be")
        self.write_u32(len(encoded) // 2)
        self.write(encoded)

    def pad(self, boundary: int = 2) -> None:
        remainder = self.tell() % boundary
        if remainder:
            self.write(b"\x00" * (boundary - remainder))
