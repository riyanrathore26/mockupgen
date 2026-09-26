# mockupgen

From-scratch PSD smart-object mockup engine.

## Goal

Open a PSD mockup template → replace the image inside a named smart object (`front`, `left`, `right`, `back`, ...) → **export a composite PNG** (or optionally save the PSD).

## Scope

- PSD only (no top-level PSB)
- Smart-object replacement is the core feature
- Consistent layer names: `front`, `left`, `right`, `back`, etc.
- Everything built from scratch (no external PSD libraries)

## Install

```bash
pip install -e .
# or just
pip install Pillow   # only runtime dependency
```

## Quick start

```python
from mockupgen import Mockup, setup_logging

setup_logging("DEBUG")

m = Mockup.open("tshirt.psd")
print(m.list_smart_objects())          # ['front', 'left', 'right', ...]

# Replace with your design
m.replace_smart_object("front", "my_design.png")

# Export full composite (perspective warp + normal blend) — no Photoshop needed
m.export("tests/exported.png")

# Optional: also write the modified PSD
# m.save("output.psd")
```

## How to test

```bash
python run_test.py
# → writes tests/exported.png
```

### What export() does (Phase 5 v1)

1. Decodes the document's flattened composite image.
2. Reads the 4-corner transform from each smart object's `PlLd` block.
3. Perspective-warps the replacement design onto those corners.
4. Alpha-composites with the layer opacity (normal blend only).
5. Writes PNG (or JPEG if path ends in `.jpg`).

Mesh warps and advanced blend modes are not implemented yet.

## Status

| Phase | Description                              | Status   |
|-------|------------------------------------------|----------|
| 1     | Binary foundations + all 5 section readers | ✅     |
| 2     | Layer records + smart-object detection   | ✅       |
| 3     | lnk2 parse + extract/replace + save      | ✅       |
| 4     | Full clean PSD writer (round-trip)       | pending  |
| 5     | Composite / export (warps, blend modes)  | ✅ v1 (perspective + normal) |

## Project layout

```
src/mockupgen/
├── api.py                 # Mockup public API (open / replace / save / export)
├── log.py                 # logging helpers
├── binary/                # BinaryReader / BinaryWriter
├── psd/
│   ├── header.py
│   ├── color_mode.py
│   ├── image_resources.py
│   ├── layer_mask.py      # layers + tagged blocks
│   ├── image_data.py
│   ├── smart_object.py    # lnk2 + replace
│   ├── placed_layer.py    # PlLd transform corners
│   ├── composite.py       # export / warp / composite
│   └── document.py
└── compression/packbits.py
```
