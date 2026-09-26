# mockupgen

From-scratch PSD smart-object mockup engine.

## Goal

Open a PSD mockup template → replace the image inside a named smart object (`front`, `left`, `right`, `back`, ...) → export the final composite image.

## Scope (intentionally limited)

- PSD only (no top-level PSB)
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
│       │   ├── reader.py          # low-level binary reader helpers
│       │   └── writer.py          # low-level binary writer helpers
│       ├── psd/
│       │   ├── header.py          # File Header (26 bytes)
│       │   ├── color_mode.py      # Color Mode Data section
│       │   ├── image_resources.py # Image Resources section
│       │   ├── layer_mask.py      # Layer & Mask Information (core)
│       │   ├── image_data.py      # Flattened composite Image Data
│       │   ├── smart_object.py    # Smart Object + Linked Layers logic
│       │   └── document.py        # High-level PSD document
│       ├── compression/
│       │   └── packbits.py        # PackBits RLE
│       └── api.py                 # Public high-level API
└── tests/
```

## Planned high-level API

```python
from mockupgen import Mockup

m = Mockup.open("tshirt.psd")
print(m.list_smart_objects())              # ['front', 'left', 'right']
m.replace_smart_object("front", "design.png")
m.save("output.psd")                       # optional
m.export("result.png")                     # final mockup screenshot
```

## Status

- **Phase 1** — binary foundations + header + all 5 section readers  ✅
- **Phase 2** — full layer-record parsing + smart-object detection by name  ✅ (this commit)
- **Phase 3** — Linked-file (lnk2) parsing + replace embedded image inside smart object
- **Phase 4** — PSD writer (round-trip save)
- **Phase 5** — composite / export (hardest: warps + blend modes)

## Current capabilities

```python
from mockupgen import Mockup

m = Mockup.open("your_mockup.psd")
print(m)                       # <Mockup 896x1200 smart_objects=['front', 'left', 'right']>
print(m.list_layers())
print(m.list_smart_objects())  # ['front', 'left', 'right']
layer = m.find_layer("front")
print(layer.is_smart_object)   # True
print(layer.placed_layer_uuid())
```
