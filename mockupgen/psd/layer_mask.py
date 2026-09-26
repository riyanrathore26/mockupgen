"""Layer and Mask Information section — Phase 2.

Parses:
- Layer count + layer records (bounds, channels, blend mode, opacity, flags, name)
- Extra data (mask, blending ranges, tagged blocks)
- Global layer mask
- Additional layer info (document-level tagged blocks, including lnk2 Linked Layers)

Smart-object layers are identified by:
  - name (front / left / right / back …)
  - presence of SoLd / PlLd tagged blocks
  - layer flags bit for smart object
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mockupgen.binary.reader import BinaryReader


# ---------------------------------------------------------------------------
# Tagged block keys we care about
# ---------------------------------------------------------------------------

KEY_PLACED_LAYER = b"PlLd"          # Placed Layer (smart object transform)
KEY_PLACED_LAYER_OLD = b"plLd"
KEY_SMART_OBJECT = b"SoLd"          # Smart Object Layer Data
KEY_SMART_OBJECT_OLD = b"SoLE"
KEY_LINKED_LAYER2 = b"lnk2"         # Document-level linked files (embedded)
KEY_LINKED_LAYER_E = b"lnkE"
KEY_UNICODE_NAME = b"luni"
KEY_LAYER_ID = b"lyid"
KEY_SECTION_DIVIDER = b"lsct"
KEY_LAYER_NAME_SOURCE = b"lnsr"

# Layer flags bits (from Adobe)
FLAG_TRANSPARENCY_PROTECTED = 1 << 0
FLAG_VISIBLE = 1 << 1               # note: 0 means visible in some docs; we treat bit as stored
FLAG_OBSOLETE = 1 << 2
FLAG_PIXEL_DATA_IRRELEVANT = 1 << 3  # often set for smart objects / groups
FLAG_GROUP = 1 << 4                 # (undocumented in some places)


@dataclass
class ChannelInfo:
    channel_id: int   # -1 = transparency, -2 = user mask, -3 = real user mask, 0+ = color
    data_length: int  # length of the channel image data that follows later


@dataclass
class TaggedBlock:
    signature: bytes          # b'8BIM' or b'8B64'
    key: bytes                # 4-byte key
    data: bytes
    # For large keys the length field was 8 bytes; we store only the payload.


@dataclass
class LayerRecord:
    """One layer as stored in the Layer Info sub-section."""

    top: int = 0
    left: int = 0
    bottom: int = 0
    right: int = 0
    channels: list[ChannelInfo] = field(default_factory=list)
    blend_mode_sig: bytes = b"8BIM"
    blend_mode_key: bytes = b"norm"
    opacity: int = 255
    clipping: int = 0
    flags: int = 0
    name: str = ""                    # from Pascal string (legacy)
    unicode_name: str = ""            # from 'luni' tagged block (preferred)
    layer_id: Optional[int] = None
    tagged_blocks: list[TaggedBlock] = field(default_factory=list)

    # Derived / convenience
    @property
    def display_name(self) -> str:
        return self.unicode_name or self.name or "(unnamed)"

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def is_smart_object(self) -> bool:
        """Heuristic: has PlacedLayer / SmartObject layer data, or zero bounds + pixel-irrelevant flag."""
        keys = {b.key for b in self.tagged_blocks}
        if KEY_PLACED_LAYER in keys or KEY_PLACED_LAYER_OLD in keys:
            return True
        if KEY_SMART_OBJECT in keys or KEY_SMART_OBJECT_OLD in keys:
            return True
        # Fallback used by many templates: empty bounds + pixel data irrelevant
        if (self.top == 0 and self.left == 0 and self.bottom == 0 and self.right == 0
                and (self.flags & FLAG_PIXEL_DATA_IRRELEVANT)):
            return True
        return False

    def get_tagged_block(self, key: bytes) -> Optional[TaggedBlock]:
        for b in self.tagged_blocks:
            if b.key == key:
                return b
        return None

    def placed_layer_uuid(self) -> Optional[str]:
        """Extract the unique ID string from a PlLd / SoLd block if present."""
        for key in (KEY_PLACED_LAYER, KEY_PLACED_LAYER_OLD, KEY_SMART_OBJECT, KEY_SMART_OBJECT_OLD):
            block = self.get_tagged_block(key)
            if block is None:
                continue
            # Payload typically starts with 4-byte type ('plcL' / 'soLD') then version,
            # then a length-prefixed unique ID that begins with '$'.
            data = block.data
            if len(data) < 20:
                continue
            # Skip type (4) + version (4) → look for '$'
            dollar = data.find(b"$")
            if dollar < 0:
                continue
            # UUID is ASCII until next null or control
            end = dollar + 1
            while end < len(data) and 32 <= data[end] < 127:
                end += 1
            uid = data[dollar:end].decode("ascii", errors="replace")
            if len(uid) > 10:
                return uid
        return None


@dataclass
class LayerAndMaskInfo:
    """Section 4 of a PSD file."""

    layers: list[LayerRecord] = field(default_factory=list)
    # Raw channel image data that follows the layer records (we keep it opaque for now)
    channel_image_data: bytes = field(default_factory=bytes)
    # Document-level additional layer info (contains lnk2, global masks, etc.)
    additional_layer_info: list[TaggedBlock] = field(default_factory=list)
    # Keep the original section bytes so we can still round-trip before full writer is ready
    raw: bytes = field(default_factory=bytes)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def find_by_name(self, name: str) -> Optional[LayerRecord]:
        name_lower = name.lower()
        for layer in self.layers:
            if layer.display_name.lower() == name_lower:
                return layer
        return None

    def smart_object_layers(self) -> list[LayerRecord]:
        return [L for L in self.layers if L.is_smart_object]

    def linked_layers_block(self) -> Optional[TaggedBlock]:
        """Return the document-level lnk2 (or lnkE) block that holds embedded files."""
        for b in self.additional_layer_info:
            if b.key in (KEY_LINKED_LAYER2, KEY_LINKED_LAYER_E):
                return b
        return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def read(cls, reader: BinaryReader) -> "LayerAndMaskInfo":
        section_length = reader.read_u32()
        section_start = reader.position
        section_end = section_start + section_length

        if section_length == 0:
            return cls(raw=b"")

        # Keep a copy of the whole section for later round-trip / debugging
        raw = reader._data[section_start:section_end].tobytes()

        # ---- Layer info -------------------------------------------------
        layer_info_length = reader.read_u32()
        layer_info_end = reader.position + layer_info_length

        layer_count = reader.read_i16()          # negative = first alpha is transparency
        abs_count = abs(layer_count)

        layers: list[LayerRecord] = []
        for _ in range(abs_count):
            layers.append(_read_layer_record(reader))

        # Channel image data sits between the end of the layer records
        # and the end of the Layer Info sub-section.
        channel_image_data = reader.read(max(0, layer_info_end - reader.position))

        # ---- Global layer mask (rarely used) ---------------------------
        if reader.position + 4 <= section_end:
            global_mask_len = reader.read_u32()
            if global_mask_len:
                reader.skip(global_mask_len)

        # ---- Additional Layer Information (document level) -------------
        # Contains lnk2, Patterns, etc.  Keys may use 4-byte or 8-byte length.
        additional: list[TaggedBlock] = []
        while reader.position + 12 <= section_end:
            block = _read_tagged_block(reader, section_end)
            if block is None:
                break
            additional.append(block)

        # Safety: land exactly at section end
        if reader.position != section_end:
            reader.seek(section_end)

        return cls(
            layers=layers,
            channel_image_data=channel_image_data,
            additional_layer_info=additional,
            raw=raw,
        )


def _read_layer_record(reader: BinaryReader) -> LayerRecord:
    top = reader.read_i32()
    left = reader.read_i32()
    bottom = reader.read_i32()
    right = reader.read_i32()

    num_channels = reader.read_u16()
    channels: list[ChannelInfo] = []
    for _ in range(num_channels):
        cid = reader.read_i16()
        clen = reader.read_u32()
        channels.append(ChannelInfo(channel_id=cid, data_length=clen))

    blend_sig = reader.read(4)
    blend_key = reader.read(4)
    opacity = reader.read_u8()
    clipping = reader.read_u8()
    flags = reader.read_u8()
    reader.read_u8()  # filler (must be 0)

    extra_len = reader.read_u32()
    extra_end = reader.position + extra_len

    # ---- Layer mask data -----------------------------------------------
    mask_len = reader.read_u32()
    if mask_len:
        reader.skip(mask_len)

    # ---- Blending ranges -----------------------------------------------
    blend_range_len = reader.read_u32()
    if blend_range_len:
        reader.skip(blend_range_len)

    # ---- Legacy Pascal name --------------------------------------------
    name = reader.read_pascal_string(pad_to=4)

    # ---- Remaining extra data = tagged blocks --------------------------
    tagged: list[TaggedBlock] = []
    while reader.position + 12 <= extra_end:
        block = _read_tagged_block(reader, extra_end)
        if block is None:
            break
        tagged.append(block)

    if reader.position != extra_end:
        reader.seek(extra_end)

    # Pull unicode name + layer id from tagged blocks
    unicode_name = ""
    layer_id = None
    for b in tagged:
        if b.key == KEY_UNICODE_NAME and len(b.data) >= 4:
            # data is itself a unicode string (u32 length + UTF-16BE)
            r = BinaryReader(b.data)
            unicode_name = r.read_unicode_string()
        elif b.key == KEY_LAYER_ID and len(b.data) >= 4:
            layer_id = struct_unpack_u32(b.data[:4])

    return LayerRecord(
        top=top,
        left=left,
        bottom=bottom,
        right=right,
        channels=channels,
        blend_mode_sig=blend_sig,
        blend_mode_key=blend_key,
        opacity=opacity,
        clipping=clipping,
        flags=flags,
        name=name,
        unicode_name=unicode_name,
        layer_id=layer_id,
        tagged_blocks=tagged,
    )


def _read_tagged_block(reader: BinaryReader, absolute_end: int) -> Optional[TaggedBlock]:
    """Read one 8BIM / 8B64 tagged block. Returns None on failure / end."""
    if reader.position + 12 > absolute_end:
        return None

    signature = reader.read(4)
    if signature not in (b"8BIM", b"8B64"):
        # Not a tagged block — rewind so caller can stop
        reader.seek(reader.position - 4)
        return None

    key = reader.read(4)

    # Large-data keys use an 8-byte length field when signature is 8B64
    # or when the key is known to be large (lnk2, lnkE, lnk3, …).
    large_keys = {b"lnk2", b"lnkE", b"lnk3", b"lnkD", b"FMsk", b"cinf", b"Txt2"}
    use_8byte = signature == b"8B64" or key in large_keys

    if use_8byte:
        if reader.position + 8 > absolute_end:
            return None
        length = reader.read_u64()
    else:
        length = reader.read_u32()

    # Safety clamp
    remaining = absolute_end - reader.position
    if length > remaining:
        length = remaining

    data = reader.read(length) if length else b""

    # Padding to even boundary (for 4-byte length case)
    if not use_8byte and length % 2 == 1:
        if reader.position < absolute_end:
            reader.skip(1)

    return TaggedBlock(signature=signature, key=key, data=data)


def struct_unpack_u32(b: bytes) -> int:
    import struct
    return struct.unpack(">I", b)[0]
