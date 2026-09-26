"""Public high-level API for mockupgen.

Target usage:

    from mockupgen import Mockup

    m = Mockup.open("tshirt.psd")
    print(m.list_smart_objects())          # ['front', 'left', 'right']
    m.replace_smart_object("front", "design.png")
    m.save("output.psd")                   # optional
    m.export("result.png")                 # final mockup screenshot
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Optional

from mockupgen.psd.document import PSDDocument
from mockupgen.psd.layer_mask import LayerRecord


class Mockup:
    """High-level facade for the mockup workflow."""

    def __init__(self, document: PSDDocument):
        self._doc = document

    @classmethod
    def open(cls, source: str | Path | bytes | BinaryIO) -> "Mockup":
        doc = PSDDocument.open(source)
        return cls(doc)

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def width(self) -> int:
        return self._doc.width

    @property
    def height(self) -> int:
        return self._doc.height

    def list_layers(self) -> list[str]:
        return [L.display_name for L in self._doc.layers()]

    def list_smart_objects(self) -> list[str]:
        """Return the names of all detected smart-object layers
        (expected to be 'front', 'left', 'right', 'back', …)."""
        return [L.display_name for L in self._doc.smart_objects()]

    def find_layer(self, name: str) -> Optional[LayerRecord]:
        return self._doc.find_layer(name)

    # ------------------------------------------------------------------
    # Core workflow
    # ------------------------------------------------------------------

    def replace_smart_object(self, name: str, image_path: str | Path) -> None:
        """Replace the content of the smart object named `name`
        (e.g. 'front', 'left', 'right', 'back') with the given image.

        Steps (implemented incrementally):
        1. Locate the LayerRecord by name and confirm it is a smart object.
        2. Extract its UUID from the PlacedLayer / SoLd tagged block.
        3. Locate the matching linked-file entry inside the document-level lnk2 block.
        4. Replace the embedded binary with a new minimal PSD/PNG of the design.
        5. Update sizes so the file stays valid.
        """
        layer = self._doc.find_layer(name)
        if layer is None:
            available = self.list_smart_objects()
            raise KeyError(
                f"No layer named {name!r}. Smart objects present: {available}"
            )
        if not layer.is_smart_object:
            raise ValueError(f"Layer {name!r} is not a smart object")

        uuid = layer.placed_layer_uuid()
        if uuid is None:
            raise RuntimeError(
                f"Could not extract UUID from smart object layer {name!r}"
            )

        # The actual binary replace will be filled in next iteration
        # once LinkedFile parsing is complete.
        raise NotImplementedError(
            f"Located smart object {name!r} (uuid={uuid}). "
            "Linked-file binary replacement is the next step."
        )

    def save(self, path: str | Path) -> None:
        """Write the modified PSD back to disk.

        Not yet implemented — requires a full writer that can rebuild
        the Layer & Mask section with updated linked data.
        """
        raise NotImplementedError("PSD writing comes after linked-file replace.")

    def export(self, path: str | Path) -> None:
        """Composite the final mockup and save as PNG/JPG.

        Full re-compositing (warps, blend modes, layer effects) is the
        hardest remaining piece.  For now this is a placeholder.
        """
        raise NotImplementedError("Compositing / export comes in a later phase.")

    def __repr__(self) -> str:
        so = self.list_smart_objects()
        return f"<Mockup {self.width}x{self.height} smart_objects={so}>"
