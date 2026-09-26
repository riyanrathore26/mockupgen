"""Public high-level API for mockupgen."""

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
        self._replacements: dict[str, Path] = {}
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
        """Replace smart-object content with the given image (PNG embed).

        The design is fitted onto the native smart-object canvas size (from the
        embedded PSB/PNG) so export warps match Photopea/Photoshop placement.
        """
        import tempfile

        layer, linked_file = self._resolve(name)
        log.info(
            "replace_smart_object(%r) uuid=%s current_file=%r",
            name,
            linked_file.uuid,
            linked_file.filename,
        )
        image_path = Path(image_path)

        # Fit design onto the smart object's native pixel canvas
        dims = so_mod.linked_file_dimensions(linked_file)
        if dims and dims[0] > 0 and dims[1] > 0:
            sow, soh = dims
            prepared = so_mod.prepare_design_on_canvas(image_path, sow, soh)
            tmp = Path(tempfile.mkdtemp(prefix="mockupgen_")) / f"{name}_prepared.png"
            prepared.save(tmp, "PNG")
            embed_path = tmp
            log.info("Using native SO canvas %dx%d for %r", sow, soh, name)
        else:
            embed_path = image_path
            log.warning(
                "Could not read native SO size for %r — embedding image as-is",
                name,
            )

        new_data = so_mod.image_to_embed_bytes(embed_path)
        new_raw = so_mod.replace_linked_file_data(
            raw_psd=bytes(self._raw),
            linked_files=self._ensure_linked(),
            target=linked_file,
            new_file_data=new_data,
        )
        self._raw = bytearray(new_raw)
        self._dirty = True
        self._linked = None
        # Export must use the prepared (SO-sized) image, not the raw texture
        self._replacements[name] = embed_path
        log.info("Replacement done — call save() and/or export()")

    def save(self, path: str | Path) -> None:
        path = Path(path)
        log.info(
            "Saving PSD to %s (%d bytes, dirty=%s)", path, len(self._raw), self._dirty
        )
        path.write_bytes(self._raw)
        log.info("Saved successfully")
        self._dirty = False

    def export(self, path: str | Path, *, fabric_strength: float | None = None) -> Path:
        """Export composite PNG/JPEG.

        Args:
            path: Output path (.png or .jpg).
            fabric_strength: 0 = clean flat design (recommended default for
                solid colors). ~0.25 = subtle fabric folds. Omit to use
                package default (0.25).
        """
        from PIL import Image
        from mockupgen.psd.composite import (
            composite_document,
            linked_file_to_image,
            decode_image_data,
        )
        from mockupgen.psd.document import PSDDocument

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("export() → %s", path)

        original_composite = decode_image_data(self._doc)
        doc = PSDDocument.open(bytes(self._raw))

        overrides: dict[str, Image.Image] = {}
        for name, img_path in self._replacements.items():
            log.debug("Loading override for %r from %s", name, img_path)
            overrides[name] = Image.open(img_path).convert("RGBA")

        if not overrides:
            linked = self._ensure_linked()
            for layer in doc.smart_objects():
                uuid = layer.placed_layer_uuid()
                lf = linked.find_by_uuid(uuid) if uuid else None
                if lf is None:
                    lf = linked.find_by_filename(layer.display_name)
                if lf is None:
                    continue
                img = linked_file_to_image(lf.file_data)
                if img is not None:
                    overrides[layer.display_name] = img

        kwargs = dict(overrides=overrides, original_composite=original_composite)
        if fabric_strength is not None:
            kwargs["fabric_strength"] = fabric_strength
        result = composite_document(doc, **kwargs)

        suffix = path.suffix.lower()
        if suffix in (".jpg", ".jpeg"):
            result = result.convert("RGB")
            result.save(path, quality=95)
        else:
            if suffix != ".png":
                path = path.with_suffix(".png")
            result.save(path)

        log.info("Exported %s (%dx%d)", path, result.width, result.height)
        return path

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
