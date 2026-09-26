"""PackBits RLE codec (compression type 1)."""

from __future__ import annotations


def decode_packbits(data: bytes, expected_size: int) -> bytes:
    """Decode PackBits RLE data into a raw byte buffer of expected_size."""
    out = bytearray()
    i = 0
    n = len(data)

    while i < n and len(out) < expected_size:
        header = data[i]
        i += 1

        if header > 128:  # 129..255 → repeat next byte (257 - header) times
            count = 257 - header
            if i >= n:
                break
            out.extend([data[i]] * count)
            i += 1
        elif header < 128:  # 0..127 → copy next (header + 1) bytes literally
            count = header + 1
            out.extend(data[i : i + count])
            i += count
        # header == 128 is a no-op (ignored)

    return bytes(out[:expected_size])


def encode_packbits(data: bytes) -> bytes:
    """Encode raw bytes with PackBits RLE.

    Simple implementation; can be optimised later.
    """
    if not data:
        return b""

    out = bytearray()
    i = 0
    n = len(data)

    while i < n:
        # try to find a run
        run_len = 1
        while i + run_len < n and data[i] == data[i + run_len] and run_len < 128:
            run_len += 1

        if run_len > 1:
            out.append(257 - run_len)
            out.append(data[i])
            i += run_len
        else:
            # literal sequence
            start = i
            i += 1
            while i < n and (i + 1 >= n or data[i] != data[i + 1]) and (i - start) < 128:
                i += 1
            length = i - start
            out.append(length - 1)
            out.extend(data[start:i])

    return bytes(out)
