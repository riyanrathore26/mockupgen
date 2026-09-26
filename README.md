# mockupgen

From-scratch PSD smart-object mockup engine.

## Goal

Open a PSD mockup template → replace the image inside a named smart object (`front`, `left`, `right`, `back`, ...) → save the PSD.

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

# Turn on detailed logs (optional but recommended while developing)
setup_logging("DEBUG")

m = Mockup.open("tshirt.psd")
print(m.list_smart_objects())          # ['front', 'left', 'right', ...]
print(m.list_linked_files())           # embedded files inside lnk2

# Extract the current content of a smart object (for inspection)
m.extract_smart_object("front", "front_extracted.psb")

# Replace with your design
m.replace_smart_object("front", "my_design.png")
m.save("output.psd")
```

Open `output.psd` in Photoshop — the smart object content is updated.
(Full automatic composite/export is Phase 5.)

## How to test

### 1. Unit-style smoke test (no Photoshop needed)

```bash
# from the repo root
python -c "
from mockupgen import Mockup, setup_logging
setup_logging('DEBUG')

m = Mockup.open('path/to/your/tshirt.psd')
print('Smart objects:', m.list_smart_objects())
print('Linked files:', m.list_linked_files())

# Extract
data = m.extract_smart_object('front', '/tmp/front.bin')
print('Extracted', len(data), 'bytes')

# Replace (use any PNG/JPG)
m.replace_smart_object('front', 'path/to/design.png')
m.save('/tmp/out.psd')
print('Saved /tmp/out.psd — open it in Photoshop to verify')
"
```

### 2. What to check in Photoshop

1. Open the saved PSD.
2. In the Layers panel, double-click the `front` smart object.
3. You should see your design image as the content.
4. The placement/warp on the t-shirt should still be intact (we only swap the embedded file, not the transform).

### 3. Enable debug logs

```python
from mockupgen import setup_logging
setup_logging("DEBUG")   # INFO is default; DEBUG shows every parse step
```

Logs go to stderr and look like:

```
09:20:01 [INFO] mockupgen.api: Opening PSD: tshirt.psd
09:20:01 [INFO] mockupgen.smart_object: LinkedFile[0]: filename='right.psb' ...
09:20:01 [DEBUG] mockupgen.api: Layer 'front' -> uuid $42e0...
09:20:01 [INFO] mockupgen.smart_object: Replacing linked file 'front.psb' ...
```

## Status

| Phase | Description                              | Status   |
|-------|------------------------------------------|----------|
| 1     | Binary foundations + all 5 section readers | ✅     |
| 2     | Layer records + smart-object detection   | ✅       |
| 3     | lnk2 parse + extract/replace + save      | ✅ (this commit) |
| 4     | Full clean PSD writer (round-trip)       | pending  |
| 5     | Composite / export (warps, blend modes)  | pending  |

## Project layout

```
src/mockupgen/
├── api.py                 # Mockup public API
├── log.py                 # logging helpers
├── binary/                # BinaryReader / BinaryWriter
├── psd/
│   ├── header.py
│   ├── color_mode.py
│   ├── image_resources.py
│   ├── layer_mask.py      # layers + tagged blocks
│   ├── image_data.py
│   ├── smart_object.py    # lnk2 + replace (Phase 3)
│   └── document.py
└── compression/packbits.py
```
