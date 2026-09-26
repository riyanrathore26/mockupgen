"""Smart Object + Linked Layers (lnk2) handling — Phase 3.

Responsibilities:
1. Parse the document-level lnk2 / lnkE block into LinkedFile entries
2. Map a smart-object layer UUID -> its LinkedFile
3. Extract the embedded binary (PSD/PSB/PNG/JPEG)
4. Replace that binary with a new image and patch size fields in the raw PSD
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mockupgen.binary.reader import BinaryReader
from mockupgen.log import get_logger

log = get_logger("smart_object")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LinkedFile:
    """One embedded (or linked) file inside the lnk2 block."""

    uuid: str
    filename: str
    filetype: bytes          # e.g. b'8BPB', b'8BPS', or blank for PNG/JPEG
    creator: bytes
    datasize: int            # size of the pure file payload
    file_data: bytes         # the actual embedded file bytes
    # Offsets relative to the start of the *lnk2 data payload*
    item_offset: int = 0     # where this item's size field starts
    item_size: int = 0       # declared u64 size of the whole item record
    file_offset_in_item: int = 0  # where file_data starts inside the item
    raw_item: bytes = field(default_factory=bytes, repr=False)

    @property
    def is_psd(self) -> bool:
        return self.file_data[:4] == b"8BPS"

    @property
    def is_png(self) -> bool:
        return self.file_data[:4] == b"\x89PNG"

    @property
    def is_jpeg(self) -> bool:
        return self.file_data[:3] == b"\xff\xd8\xff"


@dataclass
class LinkedFiles:
    """All linked files found in the document-level lnk2 block."""

    files: list[LinkedFile] = field(default_factory=list)
    # Absolute offsets inside the original PSD file (for surgical patching)
    lnk2_data_abs_start: int = 0   # byte offset of lnk2 payload in the PSD
    lnk2_length_field_abs: int = 0 # byte offset of the 4-byte length field
    lnk2_data_length: int = 0

    def find_by_uuid(self, uuid: str) -> Optional[LinkedFile]:
        for f in self.files:
            if f.uuid == uuid or f.uuid.lstrip("$") == uuid.lstrip("$"):
                return f
        return None

    def find_by_filename(self, name: str) -> Optional[LinkedFile]:
        name_lower = name.lower()
        for f in self.files:
            if f.filename.lower() == name_lower or f.filename.lower().startswith(name_lower + "."):
                return f
        return None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_linked_files(
    lnk2_data: bytes,
    *,
    lnk2_data_abs_start: int = 0,
    lnk2_length_field_abs: int = 0,
) -> LinkedFiles:
    """Parse the payload of an lnk2 (or lnkE) tagged block."""
    log.debug(
        "Parsing lnk2 payload: %d bytes (abs_start=%d)",
        len(lnk2_data),
        lnk2_data_abs_start,
    )

    result = LinkedFiles(
        lnk2_data_abs_start=lnk2_data_abs_start,
        lnk2_length_field_abs=lnk2_length_field_abs,
        lnk2_data_length=len(lnk2_data),
    )

    # Walk by searching for 'liFD' markers (most reliable across padding)
    search_from = 0
    while True:
        marker = lnk2_data.find(b"liFD", search_from)
        if marker < 0:
            break
        item_start = marker - 8  # size field is 8 bytes before type
        if item_start < 0:
            search_from = marker + 4
            continue

        declared_size = struct.unpack(">Q", lnk2_data[item_start : item_start + 8])[0]
        # Determine actual span to next item (or end)
        next_marker = lnk2_data.find(b"liFD", marker + 4)
        if next_marker > 0:
            actual_span = (next_marker - 8) - item_start
        else:
            actual_span = len(lnk2_data) - item_start

        # Use the larger of declared size and actual span so we don't truncate
        span = max(declared_size, actual_span)
        # But never read past the buffer
        span = min(span, len(lnk2_data) - item_start)
        item_bytes = lnk2_data[item_start : item_start + span]

        linked = _parse_single_item(item_bytes, item_offset=item_start)
        if linked is not None:
            result.files.append(linked)
            log.info(
                "LinkedFile[%d]: filename=%r uuid=%s datasize=%d type=%s",
                len(result.files) - 1,
                linked.filename,
                linked.uuid,
                linked.datasize,
                linked.filetype,
            )
        else:
            log.warning("Failed to parse liFD item at offset %d", item_start)

        search_from = marker + 4

    log.info("Parsed %d linked file(s) from lnk2", len(result.files))
    return result


def _parse_single_item(item: bytes, item_offset: int = 0) -> Optional[LinkedFile]:
    """Parse one liFD record."""
    if len(item) < 32:
        return None

    declared_size = struct.unpack(">Q", item[0:8])[0]
    typ = item[8:12]
    if typ != b"liFD":
        return None
    ver = struct.unpack(">I", item[12:16])[0]

    # Unique ID — ASCII starting with '$' until a non-printable byte
    p = 16
    while p < len(item) and 32 <= item[p] < 127:
        p += 1
    uuid = item[16:p].decode("ascii", errors="replace")
    if not uuid.startswith("$"):
        log.debug("Unexpected UUID format at item offset %d: %r", item_offset, uuid[:40])

    # Filename — u32 character count + UTF-16BE
    if p + 4 > len(item):
        return None
    fname_chars = struct.unpack(">I", item[p : p + 4])[0]
    p += 4
    if p + fname_chars * 2 > len(item):
        return None
    filename = item[p : p + fname_chars * 2].decode("utf-16-be", errors="replace").rstrip("\x00")
    p += fname_chars * 2

    if p + 16 > len(item):
        return None
    filetype = item[p : p + 4]
    creator = item[p + 4 : p + 8]
    datasize = struct.unpack(">Q", item[p + 8 : p + 16])[0]
    p += 16

    # Locate the real file payload by magic bytes
    magic_offset = -1
    for magic in (b"8BPS", b"\x89PNG", b"\xff\xd8\xff", b"GIF8"):
        mi = item.find(magic, p)
        if mi >= 0:
            magic_offset = mi
            break

    if magic_offset < 0:
        log.warning("No known file magic in linked item %r (uuid=%s)", filename, uuid)
        return None

    # Clamp datasize to available bytes
    available = len(item) - magic_offset
    if datasize > available:
        log.debug(
            "datasize %d > available %d for %r — clamping",
            datasize,
            available,
            filename,
        )
        datasize = available

    file_data = item[magic_offset : magic_offset + datasize]

    return LinkedFile(
        uuid=uuid,
        filename=filename,
        filetype=filetype,
        creator=creator,
        datasize=datasize,
        file_data=file_data,
        item_offset=item_offset,
        item_size=declared_size,
        file_offset_in_item=magic_offset,
        raw_item=item[: max(declared_size, magic_offset + datasize)],
    )


# ---------------------------------------------------------------------------
# High-level helpers used by the Mockup API
# ---------------------------------------------------------------------------


def load_linked_files_from_document(doc) -> LinkedFiles:
    """Extract and parse the lnk2 block from an already-opened PSDDocument."""
    block = doc.layer_mask.linked_layers_block()
    if block is None:
        log.error("No lnk2/lnkE block found in document")
        return LinkedFiles()

    # We need absolute offsets for surgical patching.
    # The LayerAndMaskInfo kept the raw section; the lnk2 data sits inside it.
    # Reconstruct absolute start from the original file bytes.
    raw = doc._raw
    # Search for the lnk2 signature in the whole file (reliable)
    sig = b"8BIMlnk2"
    abs_sig = raw.find(sig)
    if abs_sig < 0:
        sig = b"8BIMlnkE"
        abs_sig = raw.find(sig)
    if abs_sig < 0:
        log.error("Could not locate lnk2/lnkE signature in raw PSD")
        return LinkedFiles()

    # After signature (8) comes 4-byte length (we used 4-byte for lnk2 in this file)
    length_field_abs = abs_sig + 8
    data_len = struct.unpack(">I", raw[length_field_abs : length_field_abs + 4])[0]
    data_abs_start = length_field_abs + 4

    log.debug(
        "lnk2 found at abs=%d, length_field=%d, data_len=%d",
        abs_sig,
        length_field_abs,
        data_len,
    )

    payload = raw[data_abs_start : data_abs_start + data_len]
    return parse_linked_files(
        payload,
        lnk2_data_abs_start=data_abs_start,
        lnk2_length_field_abs=length_field_abs,
    )


def image_to_png_bytes(image_path: str | Path) -> bytes:
    """Load any image Pillow understands and return PNG bytes."""
    from PIL import Image

    path = Path(image_path)
    log.info("Loading replacement image: %s", path)
    img = Image.open(path)
    log.debug("Image mode=%s size=%s", img.mode, img.size)

    # Convert to RGBA for maximum compatibility inside smart objects
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
        log.debug("Converted to RGBA")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    log.info("Replacement PNG size: %d bytes (%dx%d)", len(data), img.width, img.height)
    return data


def replace_linked_file_data(
    raw_psd: bytes,
    linked_files: LinkedFiles,
    target: LinkedFile,
    new_file_data: bytes,
    new_filename: str | None = None,
) -> bytes:
    """Return a new PSD byte string with the target linked file's payload replaced.

    Strategy (surgical patch):
    1. Build a new item record that keeps the original header (uuid, etc.)
       but swaps the file payload and updates datasize.
    2. Splice the new item into the lnk2 payload.
    3. Update the lnk2 length field and the LayerAndMask section length
       so the rest of the file stays consistent.
    """
    log.info(
        "Replacing linked file %r (uuid=%s) old_size=%d -> new_size=%d",
        target.filename,
        target.uuid,
        target.datasize,
        len(new_file_data),
    )

    # --- rebuild the single item -----------------------------------------
    old_item = target.raw_item
    header = old_item[: target.file_offset_in_item]  # everything before file payload

    # Patch the datasize field inside the header.
    # Layout: size(8) type(4) ver(4) uuid(...) fname(...) filetype(4) creator(4) datasize(8)
    # We already know datasize sits 8 bytes before the end of the "header" region
    # that ends at file_offset_in_item, but safer to re-parse offsets.
    p = 16
    while p < len(header) and 32 <= header[p] < 127:
        p += 1
    fname_chars = struct.unpack(">I", header[p : p + 4])[0]
    p += 4 + fname_chars * 2
    # p now at filetype; datasize at p+8
    datasize_offset_in_item = p + 8
    log.debug("datasize field at item-relative offset %d", datasize_offset_in_item)

    header_mutable = bytearray(header)
    struct.pack_into(">Q", header_mutable, datasize_offset_in_item, len(new_file_data))

    # Optionally update filename (keep original if not supplied)
    # For simplicity we keep the original filename/uuid so Photoshop still matches.

    new_item_body = bytes(header_mutable) + new_file_data
    # Pad to even length (Photoshop convention)
    if len(new_item_body) % 2 == 1:
        new_item_body += b"\x00"

    # Update the leading u64 size field of the item to the new total length
    new_item = bytearray(new_item_body)
    struct.pack_into(">Q", new_item, 0, len(new_item))
    new_item = bytes(new_item)

    log.debug(
        "New item length=%d (old declared=%d)",
        len(new_item),
        target.item_size,
    )

    # --- splice into lnk2 payload ----------------------------------------
    lnk2_payload = bytearray(
        raw_psd[
            linked_files.lnk2_data_abs_start : linked_files.lnk2_data_abs_start
            + linked_files.lnk2_data_length
        ]
    )

    old_span = target.item_size
    # Prefer the actual span we observed when parsing
    next_off = None
    for f in linked_files.files:
        if f.item_offset > target.item_offset:
            if next_off is None or f.item_offset < next_off:
                next_off = f.item_offset
    if next_off is not None:
        old_span = next_off - target.item_offset
    else:
        old_span = linked_files.lnk2_data_length - target.item_offset

    log.debug(
        "Splicing: item_offset=%d old_span=%d new_len=%d",
        target.item_offset,
        old_span,
        len(new_item),
    )

    new_payload = (
        bytes(lnk2_payload[: target.item_offset])
        + new_item
        + bytes(lnk2_payload[target.item_offset + old_span :])
    )

    size_delta = len(new_payload) - linked_files.lnk2_data_length
    log.info("lnk2 payload size delta: %+d bytes", size_delta)

    # --- rebuild the full PSD --------------------------------------------
    # 1. Update the 4-byte lnk2 length field
    # 2. Replace the lnk2 payload
    # 3. Update the LayerAndMask section length (4 bytes at the start of section 4)

    raw = bytearray(raw_psd)

    # Update lnk2 data length
    struct.pack_into(">I", raw, linked_files.lnk2_length_field_abs, len(new_payload))

    # Find LayerAndMask section length field (after header + color + resources)
    # Header = 26, then color-mode length (4) + data, then resources length (4) + data
    r = BinaryReader(raw_psd)
    r.seek(26)
    cmd_len = r.read_u32()
    r.skip(cmd_len)
    ir_len = r.read_u32()
    r.skip(ir_len)
    lm_length_field_abs = r.position
    old_lm_len = r.read_u32()
    new_lm_len = old_lm_len + size_delta
    log.debug(
        "LayerAndMask length field at %d: %d -> %d",
        lm_length_field_abs,
        old_lm_len,
        new_lm_len,
    )
    struct.pack_into(">I", raw, lm_length_field_abs, new_lm_len)

    # Splice the new lnk2 payload into the file
    before = bytes(raw[: linked_files.lnk2_data_abs_start])
    after = bytes(
        raw[
            linked_files.lnk2_data_abs_start + linked_files.lnk2_data_length :
        ]
    )
    result = before + new_payload + after

    log.info(
        "Patched PSD size: %d -> %d (delta %+d)",
        len(raw_psd),
        len(result),
        len(result) - len(raw_psd),
    )
    return result
