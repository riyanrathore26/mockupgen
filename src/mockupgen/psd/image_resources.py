"""Image Resources section (placeholder for Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mockupgen.binary.reader import BinaryReader
from mockupgen.binary.writer import BinaryWriter


@dataclass
class ImageResource:
    signature: bytes  # usually b'8BIM'
    resource_id: int
    name: str
    data: bytes


@dataclass
class ImageResources:
    """Section 3 of a PSD file — non-pixel metadata blocks."""

    resources: list[ImageResource] = field(default_factory=list)

    @classmethod
    def read(cls, reader: BinaryReader) -> "ImageResources":
        section_length = reader.read_u32()
        end = reader.position + section_length

        resources: list[ImageResource] = []
        while reader.position < end:
            signature = reader.read(4)
            resource_id = reader.read_u16()
            name = reader.read_pascal_string(pad_to=2)
            data_len = reader.read_u32()
            data = reader.read(data_len)
            # data is padded to even length
            if data_len % 2 == 1:
                reader.skip(1)
            resources.append(
                ImageResource(
                    signature=signature,
                    resource_id=resource_id,
                    name=name,
                    data=data,
                )
            )

        # safety: make sure we landed exactly at the end
        if reader.position != end:
            reader.seek(end)

        return cls(resources=resources)

    def write(self, writer: BinaryWriter) -> None:
        # Write into a temporary buffer so we know the length
        inner = BinaryWriter()
        for res in self.resources:
            inner.write(res.signature)
            inner.write_u16(res.resource_id)
            inner.write_pascal_string(res.name, pad_to=2)
            inner.write_u32(len(res.data))
            inner.write(res.data)
            if len(res.data) % 2 == 1:
                inner.write(b"\x00")

        payload = inner.getvalue()
        writer.write_u32(len(payload))
        writer.write(payload)
