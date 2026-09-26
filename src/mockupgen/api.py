"""Public high-level API for mockupgen.

Target usage:

    from mockupgen import Mockup

    m = Mockup.open("tshirt.psd")
    m.replace_smart_object("front", "design.png")
    m.export("result.png")
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from mockupgen.psd.document import PSDDocument


class Mockup:
    """High-level facade for the mockup workflow."""

    def __init__(self, document: PSDDocument):
        self._doc = document

    @classmethod
    def open(cls, source: str | Path | bytes | BinaryIO) -> "Mockup":
        doc = PSDDocument.open(source)
        return cls(doc)

    @property
    def width(self) -> int:
        return self._doc.width

    @property
    def height(self) -> int:
        return self._doc.height

    def replace_smart_object(self, name: str, image_path: str | Path) -> None:
        """Replace the content of the smart object named `name`
        (e.g. 'front', 'left', 'right', 'back') with the given image.

        Not yet implemented — coming in Phase 3.
        """
        raise NotImplementedError(
            "Smart-object replacement will be implemented after "
            "layer & linked-file parsing is complete."
        )

    def save(self, path: str | Path) -> None:
        """Write the modified PSD back to disk.

        Not yet implemented.
        """
        raise NotImplementedError("PSD writing comes in a later phase.")

    def export(self, path: str | Path) -> None:
        """Composite the final mockup and save as PNG/JPG.

        Not yet implemented.
        """
        raise NotImplementedError("Compositing / export comes in a later phase.")

    def __repr__(self) -> str:
        return f"<Mockup {self.width}x{self.height}>"
