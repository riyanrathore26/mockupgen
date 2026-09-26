"""High-level PSD document — entry point for opening a file."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Optional

from mockupgen.binary.reader import BinaryReader
from mockupgen.psd.header import FileHeader
from mockupgen.psd.color_mode import ColorModeData
from mockupgen.psd.image_resources import ImageResources
from mockupgen.psd.layer_mask import LayerAndMaskInfo, LayerRecord
from mockupgen.psd.image_data import ImageData


class PSDDocument:
    """Represents an open PSD file with all five sections parsed."""

    def __init__(
        self,
        header: FileHeader,
        color_mode: ColorModeData,
        image_resources: ImageResources,
        layer_mask: LayerAndMaskInfo,
        image_data: ImageData,
        raw: bytes,
    ):
        self.header = header
        self.color_mode = color_mode
        self.image_resources = image_resources
        self.layer_mask = layer_mask
        self.image_data = image_data
        self._raw = raw

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
        color_mode = ColorModeData.read(reader)
        image_resources = ImageResources.read(reader)
        layer_mask = LayerAndMaskInfo.read(reader)
        image_data = ImageData.read(reader)

        return cls(
            header=header,
            color_mode=color_mode,
            image_resources=image_resources,
            layer_mask=layer_mask,
            image_data=image_data,
            raw=data,
        )

    # ------------------------------------------------------------------
    # Convenience
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
    def color_mode_id(self) -> int:
        return self.header.color_mode

    def layers(self) -> list[LayerRecord]:
        return self.layer_mask.layers

    def find_layer(self, name: str) -> Optional[LayerRecord]:
        return self.layer_mask.find_by_name(name)

    def smart_objects(self) -> list[LayerRecord]:
        return self.layer_mask.smart_object_layers()

    def __repr__(self) -> str:
        so = len(self.smart_objects())
        return (
            f"<PSDDocument {self.header} "
            f"layers={len(self.layers())} smart_objects={so}>"
        )
