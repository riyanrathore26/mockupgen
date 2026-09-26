"""Smart Object + Linked Layers (lnk2) handling — Phase 3 (fixed).

Responsibilities:
1. Parse the document-level lnk2 / lnkE block into LinkedFile entries
2. Map a smart-object layer UUID -> its LinkedFile
3. Extract the embedded binary (PSD/PSB/PNG/JPEG)
4. Replace that binary with a new image (PNG) and patch size fields
   so Photopea / Photoshop still resolve all UUIDs

Important size-field rule (Adobe):
  The u64 at the start of each liFD item is the length of everything
  AFTER that u64 (i.e. it does NOT include itself).

Photopea notes:
  - Embedding a huge raw PSD (e.g. 4800x7500 = 108MB) often shows as "damaged"
  - This mockup already has a PNG linked file, so PNG embeds are accepted
  - Large designs are auto-resized (long edge capped) for mockup use
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

# Max long-edge for embedded design. Mockup smart-object canvases are small
# (often ~400–800px). Huge textures only bloat the file and break Photopea.
MAX_DESIGN_EDGE = 2500


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LinkedFile:
    """One embedded (or linked) file inside the lnk2 block."""

    uuid: str
    filename: str
    filetype: bytes  # e.g. b'8BPB', b'8BPS', b'png '
    creator: bytes
    datasize: int
    file_data: bytes
    item_offset: int = 0
    item_size: int = 0  # declared u64 (excludes the 8-byte size field)
    actual_span: int = 0  # real bytes until next item (includes padding)
    file_offset_in_item: int = 0
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
    files: list[LinkedFile] = field(default_factory=list)
    lnk2_data_abs_start: int = 0
    lnk2_length_field_abs: int = 0
    lnk2_data_length: int = 0

    def find_by_uuid(self, uuid: str) -> Optional[LinkedFile]:
        for f in self.files:
            if f.uuid == uuid or f.uuid.lstrip("$") == uuid.lstrip("$"):
                return f
        return None

    def find_by_filename(self, name: str) -> Optional[LinkedFile]:
        name_lower = name.lower()
        for f in self.files:
            if f.filename.lower() == name_lower or f.filename.lower().startswith(
                name_lower + "."
            ):
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

    search_from = 0
    while True:
        marker = lnk2_data.find(b"liFD", search_from)
        if marker < 0:
            break
        item_start = marker - 8
        if item_start < 0:
            search_from = marker + 4
            continue

        declared_size = struct.unpack(">Q", lnk2_data[item_start : item_start + 8])[0]
        next_marker = lnk2_data.find(b"liFD", marker + 4)
        if next_marker > 0:
            actual_span = (next_marker - 8) - item_start
        else:
            actual_span = len(lnk2_data) - item_start

        span = min(actual_span, len(lnk2_data) - item_start)
        item_bytes = lnk2_data[item_start : item_start + span]

        linked = _parse_single_item(
            item_bytes, item_offset=item_start, actual_span=actual_span
        )
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


def _parse_single_item(
    item: bytes, item_offset: int = 0, actual_span: int = 0
) -> Optional[LinkedFile]:
    if len(item) < 32:
        return None

    declared_size = struct.unpack(">Q", item[0:8])[0]
    typ = item[8:12]
    if typ != b"liFD":
        return None

    p = 16
    while p < len(item) and 32 <= item[p] < 127:
        p += 1
    uuid = item[16:p].decode("ascii", errors="replace")

    if p + 4 > len(item):
        return None
    fname_chars = struct.unpack(">I", item[p : p + 4])[0]
    p += 4
    if p + fname_chars * 2 > len(item):
        return None
    filename = (
        item[p : p + fname_chars * 2]
        .decode("utf-16-be", errors="replace")
        .rstrip("\x00")
    )
    p += fname_chars * 2

    if p + 16 > len(item):
        return None
    filetype = item[p : p + 4]
    creator = item[p + 4 : p + 8]
    datasize = struct.unpack(">Q", item[p + 8 : p + 16])[0]
    p += 16

    magic_offset = -1
    for magic in (b"8BPS", b"\x89PNG", b"\xff\xd8\xff", b"GIF8"):
        mi = item.find(magic, p)
        if mi >= 0:
            magic_offset = mi
            break

    if magic_offset < 0:
        log.warning(
            "No known file magic in linked item %r (uuid=%s)", filename, uuid
        )
        return None

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
        actual_span=actual_span or len(item),
        file_offset_in_item=magic_offset,
        raw_item=item,
    )


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------


def load_linked_files_from_document(doc) -> LinkedFiles:
    block = doc.layer_mask.linked_layers_block()
    if block is None:
        log.error("No lnk2/lnkE block found in document")
        return LinkedFiles()

    raw = doc._raw
    sig = b"8BIMlnk2"
    abs_sig = raw.find(sig)
    if abs_sig < 0:
        sig = b"8BIMlnkE"
        abs_sig = raw.find(sig)
    if abs_sig < 0:
        log.error("Could not locate lnk2/lnkE signature in raw PSD")
        return LinkedFiles()

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


def image_to_embed_bytes(image_path: str | Path) -> bytes:
    """Load an image, optionally downscale, and return PNG bytes for embedding.

    PNG is used because:
    - This mockup already contains a PNG linked file (accepted by Photopea)
    - A full-res 4800x7500 raw PSD is ~108MB and Photopea often marks it damaged
    - PNG stays small and valid
    """
    from PIL import Image

    path = Path(image_path)
    log.info("Loading replacement image: %s", path)
    img = Image.open(path)
    log.debug("Image mode=%s size=%s", img.mode, img.size)

    # Auto-resize large designs (mockup slots are typically a few hundred px)
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > MAX_DESIGN_EDGE:
        scale = MAX_DESIGN_EDGE / long_edge
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        log.info(
            "Resizing design %dx%d -> %dx%d (max edge %d)",
            w,
            h,
            new_size[0],
            new_size[1],
            MAX_DESIGN_EDGE,
        )
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
        log.debug("Converted to RGBA")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    log.info(
        "Embed PNG: %d bytes (%dx%d)", len(data), img.width, img.height
    )
    return data


# Backwards-compatible aliases
def image_to_minimal_psd(image_path: str | Path) -> bytes:
    return image_to_embed_bytes(image_path)


def image_to_png_bytes(image_path: str | Path) -> bytes:
    return image_to_embed_bytes(image_path)


def replace_linked_file_data(
    raw_psd: bytes,
    linked_files: LinkedFiles,
    target: LinkedFile,
    new_file_data: bytes,
    new_filename: str | None = None,
) -> bytes:
    """Splice new_file_data into the target linked-file slot.

    Size field rule: value = len(data after the u64), does NOT include itself.
    """
    log.info(
        "Replacing linked file %r (uuid=%s) old_size=%d -> new_size=%d",
        target.filename,
        target.uuid,
        target.datasize,
        len(new_file_data),
    )

    old_item = target.raw_item
    header = bytearray(old_item[: target.file_offset_in_item])

    # Locate filetype + datasize inside header
    p = 16
    while p < len(header) and 32 <= header[p] < 127:
        p += 1
    fname_chars = struct.unpack(">I", header[p : p + 4])[0]
    p += 4 + fname_chars * 2
    filetype_off = p
    datasize_off = p + 8

    # Match existing PNG linked file style in this mockup (type 'png ')
    if new_file_data[:4] == b"\x89PNG":
        header[filetype_off : filetype_off + 4] = b"png "
    elif new_file_data[:4] == b"8BPS":
        header[filetype_off : filetype_off + 4] = b"8BPS"
    else:
        header[filetype_off : filetype_off + 4] = b"    "

    struct.pack_into(">Q", header, datasize_off, len(new_file_data))
    log.debug(
        "Patched filetype=%r, datasize=%d",
        bytes(header[filetype_off : filetype_off + 4]),
        len(new_file_data),
    )

    body_after_size = bytes(header[8:]) + new_file_data

    # 4-byte align total item
    total_len = 8 + len(body_after_size)
    pad = (4 - (total_len % 4)) % 4
    if pad:
        body_after_size += b"\x00" * pad

    # Size EXCLUDES the 8-byte size field itself
    size_field_value = len(body_after_size)
    new_item = struct.pack(">Q", size_field_value) + body_after_size

    log.debug(
        "New item: size_field=%d total_len=%d pad=%d (old declared=%d span=%d)",
        size_field_value,
        len(new_item),
        pad,
        target.item_size,
        target.actual_span,
    )

    lnk2_payload = raw_psd[
        linked_files.lnk2_data_abs_start : linked_files.lnk2_data_abs_start
        + linked_files.lnk2_data_length
    ]

    old_span = target.actual_span
    if old_span <= 0:
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
        lnk2_payload[: target.item_offset]
        + new_item
        + lnk2_payload[target.item_offset + old_span :]
    )

    size_delta = len(new_payload) - linked_files.lnk2_data_length
    log.info("lnk2 payload size delta: %+d bytes", size_delta)

    for f in linked_files.files:
        if f.uuid.encode("ascii") not in new_payload:
            log.error("UUID %s missing after splice", f.uuid)
            raise RuntimeError(
                f"Linked-file splice lost UUID {f.uuid}. "
                "This would cause 'unknown linked layer' in Photopea."
            )

    raw = bytearray(raw_psd)

    struct.pack_into(">I", raw, linked_files.lnk2_length_field_abs, len(new_payload))

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

    before = bytes(raw[: linked_files.lnk2_data_abs_start])
    after = bytes(
        raw[linked_files.lnk2_data_abs_start + linked_files.lnk2_data_length :]
    )
    result = before + new_payload + after

    log.info(
        "Patched PSD size: %d -> %d (delta %+d)",
        len(raw_psd),
        len(result),
        len(result) - len(raw_psd),
    )
    return result
