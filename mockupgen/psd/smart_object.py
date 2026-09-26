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

MAX_DESIGN_EDGE = 2500


@dataclass
class LinkedFile:
    uuid: str
    filename: str
    filetype: bytes
    creator: bytes
    datasize: int
    file_data: bytes
    item_offset: int = 0
    item_size: int = 0
    actual_span: int = 0
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


def linked_file_dimensions(lf: "LinkedFile") -> tuple[int, int] | None:
    """Return (width, height) of an embedded linked file, if readable."""
    data = lf.file_data
    if not data:
        return None
    if data[:4] == b"8BPS" and len(data) >= 22:
        h = struct.unpack(">I", data[14:18])[0]
        w = struct.unpack(">I", data[18:22])[0]
        if 0 < w < 30000 and 0 < h < 30000:
            return (w, h)
    if data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff" or data[:4] == b"GIF8":
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            return im.size
        except Exception:
            return None
    return None


def prepare_design_on_canvas(
    image_path: str | Path,
    canvas_w: int,
    canvas_h: int,
    *,
    fill: float = 1.0,
    top_bias: float = 0.5,
) -> "object":
    """Place design on a transparent canvas matching the smart-object size.

    Fits the design into the SO canvas at the given fill ratio (default 1.0 =
    actual size, full canvas). Keeps aspect ratio and transparency. top_bias
    0.5 = vertically centered.
    """
    from PIL import Image

    img = Image.open(image_path).convert("RGBA")
    iw, ih = img.size
    if iw < 1 or ih < 1:
        return Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    fill = max(0.1, min(1.0, float(fill)))
    max_w = canvas_w * fill
    max_h = canvas_h * fill
    scale = min(max_w / iw, max_h / ih)
    nw = max(1, round(iw * scale))
    nh = max(1, round(ih * scale))
    if (nw, nh) != (iw, ih):
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    ox = (canvas_w - nw) // 2
    free_y = max(0, canvas_h - nh)
    oy = int(free_y * max(0.0, min(1.0, top_bias)))
    canvas.paste(img, (ox, oy), img)
    log.info(
        "Prepared design %dx%d -> canvas %dx%d (placed %dx%d at %d,%d, fill=%.2f)",
        iw, ih, canvas_w, canvas_h, nw, nh, ox, oy, fill,
    )
    return canvas


def parse_linked_files(
    lnk2_data: bytes,
    *,
    lnk2_data_abs_start: int = 0,
    lnk2_length_field_abs: int = 0,
) -> LinkedFiles:
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
        return None
    available = len(item) - magic_offset
    if datasize > available:
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


def load_linked_files_from_document(doc) -> LinkedFiles:
    raw = doc._raw
    block = doc.layer_mask.linked_layers_block()
    abs_sig = -1
    data_len = 0
    length_field_abs = 0
    data_abs_start = 0
    if block is not None and block.data:
        for sig in (b"8BIMlnk2", b"8BIMlnkE"):
            abs_sig = raw.find(sig)
            if abs_sig >= 0:
                break
        if abs_sig < 0:
            return parse_linked_files(block.data)
        length_field_abs = abs_sig + 8
        if raw[abs_sig:abs_sig + 4] == b"8B64":
            data_len = struct.unpack(">Q", raw[length_field_abs:length_field_abs + 8])[0]
            data_abs_start = length_field_abs + 8
        else:
            data_len = struct.unpack(">I", raw[length_field_abs:length_field_abs + 4])[0]
            data_abs_start = length_field_abs + 4
    else:
        for sig in (b"8BIMlnk2", b"8BIMlnkE"):
            abs_sig = raw.find(sig)
            if abs_sig >= 0:
                break
        if abs_sig < 0:
            log.error("No lnk2/lnkE block found in document")
            return LinkedFiles()
        length_field_abs = abs_sig + 8
        if raw[abs_sig:abs_sig + 4] == b"8B64":
            data_len = struct.unpack(">Q", raw[length_field_abs:length_field_abs + 8])[0]
            data_abs_start = length_field_abs + 8
        else:
            data_len = struct.unpack(">I", raw[length_field_abs:length_field_abs + 4])[0]
            data_abs_start = length_field_abs + 4
    if data_abs_start + data_len > len(raw):
        data_len = max(0, len(raw) - data_abs_start)
    log.info("lnk2 found at abs=%d, length_field=%d, data_len=%d", abs_sig, length_field_abs, data_len)
    payload = raw[data_abs_start : data_abs_start + data_len]
    return parse_linked_files(
        payload,
        lnk2_data_abs_start=data_abs_start,
        lnk2_length_field_abs=length_field_abs,
    )


def image_to_embed_bytes(image_path: str | Path) -> bytes:
    from PIL import Image
    path = Path(image_path)
    log.info("Loading replacement image: %s", path)
    img = Image.open(path)
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > MAX_DESIGN_EDGE:
        scale = MAX_DESIGN_EDGE / long_edge
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    log.info("Embed PNG: %d bytes (%dx%d)", len(data), img.width, img.height)
    return data


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
    log.info(
        "Replacing linked file %r (uuid=%s) old_size=%d -> new_size=%d",
        target.filename, target.uuid, target.datasize, len(new_file_data),
    )
    old_item = target.raw_item
    header = bytearray(old_item[: target.file_offset_in_item])
    p = 16
    while p < len(header) and 32 <= header[p] < 127:
        p += 1
    fname_chars = struct.unpack(">I", header[p : p + 4])[0]
    p += 4 + fname_chars * 2
    filetype_off = p
    datasize_off = p + 8
    if new_file_data[:4] == b"\x89PNG":
        header[filetype_off : filetype_off + 4] = b"png "
    elif new_file_data[:4] == b"8BPS":
        header[filetype_off : filetype_off + 4] = b"8BPS"
    else:
        header[filetype_off : filetype_off + 4] = b"    "
    struct.pack_into(">Q", header, datasize_off, len(new_file_data))
    body_after_size = bytes(header[8:]) + new_file_data
    total_len = 8 + len(body_after_size)
    pad = (4 - (total_len % 4)) % 4
    if pad:
        body_after_size += b"\x00" * pad
    size_field_value = len(body_after_size)
    new_item = struct.pack(">Q", size_field_value) + body_after_size
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
    new_payload = (
        lnk2_payload[: target.item_offset]
        + new_item
        + lnk2_payload[target.item_offset + old_span :]
    )
    size_delta = len(new_payload) - linked_files.lnk2_data_length
    log.info("lnk2 payload size delta: %+d bytes", size_delta)
    for f in linked_files.files:
        if f.uuid.encode("ascii") not in new_payload:
            raise RuntimeError(f"Linked-file splice lost UUID {f.uuid}.")
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
    struct.pack_into(">I", raw, lm_length_field_abs, new_lm_len)
    before = bytes(raw[: linked_files.lnk2_data_abs_start])
    after = bytes(raw[linked_files.lnk2_data_abs_start + linked_files.lnk2_data_length :])
    result = before + new_payload + after
    log.info("Patched PSD size: %d -> %d (delta %+d)", len(raw_psd), len(result), len(result) - len(raw_psd))
    return result
