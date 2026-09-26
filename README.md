# mockupgen

From-scratch PSD smart-object mockup engine.

## Goal

Open a PSD mockup template → replace the image inside a named smart object (`front`, `left`, `right`, `back`, ...) → export the final composite image.

## Scope (intentionally limited)

- PSD only (no PSB at top level)
- Smart-object replacement is the core feature
- Consistent layer names: `front`, `left`, `right`, `back`, etc.
- Everything built from scratch (no external PSD libraries in the final product)

## Project layout

```
mockupgen/
├── README.md
├── requirements.txt
├── pyproject.toml
├── src/
│   └── mockupgen/
│       ├── __init__.py
│       ├── binary/
│       │   ├── __init__.py
│       │   ├── reader.py          # low-level binary reader helpers
│       │   └── writer.py          # low-level binary writer helpers
│       ├── psd/
│       │   ├── __init__.py
│       │   ├── header.py          # File Header (26 bytes)
│       │   ├── color_mode.py      # Color Mode Data section
│       │   ├── image_resources.py # Image Resources section
│       │   ├── layer_mask.py      # Layer & Mask Information (core)
│       │   ├── image_data.py      # Flattened composite Image Data
│       │   ├── smart_object.py    # Smart Object + Linked Layers logic
│       │   └── document.py        # High-level PSD document
│       ├── compression/
│       │   ├── __init__.py
│       │   ├── packbits.py        # PackBits RLE
│       │   └── zip.py             # ZIP / ZIP-prediction
│       └── api.py               # Public high-level API
└── tests/
    └── ...
```

## Planned high-level API

```python
from mockupgen import Mockup

m = Mockup.open("tshirt_front.psd")
m.replace_smart_object("front", "design.png")   # or left / right / back
m.save("output.psd")                            # optional
m.export("result.png")                          # final mockup screenshot
```

## Status

Phase 1 — binary foundations + header parsing (in progress)
