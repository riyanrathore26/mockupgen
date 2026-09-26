"""Public high-level API for mockupgen.

Target usage:

    from mockupgen import Mockup, setup_logging

    setup_logging("DEBUG")

    m = Mockup.open("tshirt.psd")
    print(m.list_smart_objects())
    m.replace_smart_object("front", "design.png")
    m.save("output.psd")
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Optional

from mockupgen.log import get_logger
from mockupgen.psd.document import PSDDocument
from mockupgen.psd.layer_mask import LayerRecord
from mockupgen.psd import smart_object as so_mod

log = get_logger("api")


class Mockup:
    """High-level facade for the mockup workflow."""

    def __init__(self, document: PSDDocument):
        self._doc = document
        self._raw = bytearray(document._raw)
        self._linked: Optional[so_mod.LinkedFiles] = None
        self._dirty = False
        log.debug("Mockup created: %s", self)

    @classmethod
    def open(cls, source: str | Path | bytes | BinaryIO) -> "Mockup":
        log.info(
            "Opening PSD: %s",
            source if not isinstance(source, (bytes, bytearray)) else f"<{len(source)} bytes>",
        )
        doc = PSDDocument.open(source)
        log.info(
            "Opened %dx%d %s — %d layers, %d smart objects",
            doc.width,
            doc.height,
            doc.header.color_mode_name if hasattr(doc.header, "color_mode_name") else "",
            len(doc.layers()),
            len(doc.smart_objects()),
        )
        return cls(doc)

    @property
    def width(self) -> int:
        return self._doc.width

    @property
    def height(self) -> int:
        return self._doc.height

    def list_layers(self) -> list[str]:
        names = [L.display_name for L in self._doc.layers()]
        log.debug("list_layers -> %s", names)
        return names

    def list_smart_objects(self) -> list[str]:
        names = [L.display_name for L in self._doc.smart_objects()]
        log.debug("list_smart_objects -> %s", names)
        return names

    def find_layer(self, name: str) -> Optional[LayerRecord]:
        layer = self._doc.find_layer(name)
        log.debug("find_layer(%r) -> %s", name, layer.display_name if layer else None)
        return layer

    def list_linked_files(self) -> list[dict]:
        linked = self._ensure_linked()
        return [
            {
                "uuid": f.uuid,
                "filename": f.filename,
                "datasize": f.datasize,
                "is_psd": f.is_psd,
                "is_png": f.is_png,
                "is_jpeg": f.is_jpeg,
            }
            for f in linked.files
        ]

    def extract_smart_object(self, name: str, dest: str | Path | None = None) -> bytes:
        layer, linked_file = self._resolve(name)
        data = linked_file.file_data
        log.info(
            "Extracted smart object %r -> %d bytes (%s)",
            name,
            len(data),
            linked_file.filename,
        )
        if dest is not None:
            Path(dest).write_bytes(data)
            log.info("Wrote extracted file to %s", dest)
        return data

    def replace_smart_object(self, name: str, image_path: str | Path) -> None:
        """Replace the content of the smart object named *name* with the image.

        The image is wrapped as a minimal PSD (required by Photopea/Photoshop)
        and written into the linked-file slot. Call :meth:`save` afterwards.
        """
        layer, linked_file = self._resolve(name)
        log.info(
            "replace_smart_object(%r) uuid=%s current_file=%r",
            name,
            linked_file.uuid,
            linked_file.filename,
        )

        # Build a real PSD, not a raw PNG — avoids "unknown linked layer"
        new_data = so_mod.image_to_minimal_psd(image_path)

        new_raw = so_mod.replace_linked_file_data(
            raw_psd=bytes(self._raw),
            linked_files=self._ensure_linked(),
            target=linked_file,
            new_file_data=new_data,
        )
        self._raw = bytearray(new_raw)
        self._dirty = True
        self._linked = None  # offsets changed
        log.info("Replacement done — call save() to write the file")

    def save(self, path: str | Path) -> None:
        path = Path(path)
        log.info(
            "Saving PSD to %s (%d bytes, dirty=%s)", path, len(self._raw), self._dirty
        )
        path.write_bytes(self._raw)
        log.info("Saved successfully")
        self._dirty = False

    def export(self, path: str | Path) -> None:
        raise NotImplementedError(
            "export() / compositing is Phase 5. "
            "After replace + save, open the PSD in Photopea or Photoshop to see the result."
        )

    def _ensure_linked(self) -> so_mod.LinkedFiles:
        if self._linked is None:
            log.debug("Parsing linked files (lnk2) …")
            from mockupgen.psd.document import PSDDocument

            tmp = PSDDocument.open(bytes(self._raw))
            self._linked = so_mod.load_linked_files_from_document(tmp)
        return self._linked

    def _resolve(self, name: str) -> tuple[LayerRecord, so_mod.LinkedFile]:
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
        log.debug("Layer %r -> uuid %s", name, uuid)

        linked = self._ensure_linked()
        linked_file = linked.find_by_uuid(uuid)
        if linked_file is None:
            linked_file = linked.find_by_filename(name)
        if linked_file is None:
            known = [f"{f.filename} ({f.uuid})" for f in linked.files]
            raise RuntimeError(
                f"No linked file for uuid={uuid} (layer {name!r}). "
                f"Known linked files: {known}"
            )
        log.debug(
            "Resolved %r -> linked file %r (%d bytes)",
            name,
            linked_file.filename,
            linked_file.datasize,
        )
        return layer, linked_file

    def __repr__(self) -> str:
        so = self.list_smart_objects()
        return f"<Mockup {self.width}x{self.height} smart_objects={so} dirty={self._dirty}>"
