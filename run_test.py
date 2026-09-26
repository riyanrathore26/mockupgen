#!/usr/bin/env python3
"""Dynamic mockup runner — replaces Photopea with mockupgen.

Accepts a JSON payload (file path, stdin, or --payload string).

Usage:
    python run_test.py payload.json
    python run_test.py --payload '{"psdUrl":"...", ...}'
    python run_test.py --local
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from mockupgen import Mockup, setup_logging

DEFAULT_TIMEOUT = 120


def download(url: str, dest: Path, *, timeout: int = DEFAULT_TIMEOUT) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  \u2193 {url[:80]}{'\u2026' if len(url) > 80 else ''}")
    req = urllib.request.Request(
        url, headers={"User-Agent": "mockupgen/0.1"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Download failed ({e.code}): {url[:60]}\u2026") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Download failed: {e.reason}") from e
    dest.write_bytes(data)
    print(f"    \u2192 {dest} ({len(data):,} bytes)")
    return dest


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def resize_texture(
    image_path: Path,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """Crop content, then fit (contain) centered on a transparent SO canvas.

    Does NOT stretch to fill. A square design on a tall smart-object
    (e.g. 457x774) stays square and is centered — matching Photopea.
    """
    img = Image.open(image_path)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    bbox = img.getbbox()
    if bbox and bbox != (0, 0, img.size[0], img.size[1]):
        print(
            f"  crop content {img.size[0]}x{img.size[1]} -> "
            f"{bbox[2] - bbox[0]}x{bbox[3] - bbox[1]}"
        )
        img = img.crop(bbox)

    if not width or not height:
        out = image_path.with_name(image_path.stem + "_cropped.png")
        img.save(out, "PNG")
        return out

    iw, ih = img.size
    scale = min(width / iw, height / ih)
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    if (nw, nh) != (iw, ih):
        print(f"  fit {iw}x{ih} -> {nw}x{nh} (canvas {width}x{height})")
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ox = (width - nw) // 2
    oy = (height - nh) // 2
    canvas.paste(img, (ox, oy), img)
    if ox or oy:
        print(f"  center offset ({ox}, {oy})")

    out = image_path.with_name(image_path.stem + f"_{width}x{height}.png")
    canvas.save(out, "PNG")
    return out


def run_payload(payload: dict[str, Any], *, work_dir: Path) -> Path:
    psd_url = payload.get("psdUrl") or payload.get("psd_url")
    textures = payload.get("textures") or {}
    dimensions = (
        payload.get("smart_objects_dimensions")
        or payload.get("dimensions")
        or {}
    )
    output = payload.get("output") or str(work_dir / "exported.png")
    primary = payload.get("primarySide") or payload.get("primary_side") or "front"
    color = payload.get("color")

    if not psd_url:
        raise ValueError("payload.psdUrl is required")
    if not textures:
        raise ValueError("payload.textures must have at least one side")

    work_dir.mkdir(parents=True, exist_ok=True)

    print("\n[1/4] PSD")
    if is_url(str(psd_url)):
        psd_path = download(str(psd_url), work_dir / "mockup.psd")
    else:
        psd_path = Path(psd_url)
        if not psd_path.exists():
            raise FileNotFoundError(f"PSD not found: {psd_path}")
        print(f"  local {psd_path}")

    print("\n[2/4] Textures")
    local_textures: dict[str, Path] = {}
    for side, info in textures.items():
        if not info:
            continue
        url = info.get("textureUrl") or info.get("url") or info.get("path")
        if not url:
            print(f"  skip {side}: no textureUrl")
            continue

        if is_url(str(url)):
            ext = ".png"
            path_part = str(url).split("?")[0]
            if path_part.lower().endswith((".jpg", ".jpeg")):
                ext = ".jpg"
            elif path_part.lower().endswith(".webp"):
                ext = ".webp"
            tex_path = download(str(url), work_dir / f"texture_{side}{ext}")
        else:
            tex_path = Path(url)
            if not tex_path.exists():
                raise FileNotFoundError(f"Texture not found for {side}: {tex_path}")
            print(f"  local {side}: {tex_path}")

        dims = dimensions.get(side) or {}
        tex_path = resize_texture(
            tex_path, width=dims.get("width"), height=dims.get("height")
        )
        local_textures[side] = tex_path

    if not local_textures:
        raise ValueError("No usable textures after download/resize")

    print("\n[3/4] Replace smart objects")
    m = Mockup.open(psd_path)
    available = m.list_smart_objects()
    print(f"  smart objects in PSD: {available}")
    if color:
        print(f"  color hint: {color}  primarySide: {primary}")

    replaced = []
    for side, tex_path in local_textures.items():
        layer_name = None
        for name in available:
            if name.lower() == side.lower():
                layer_name = name
                break
        if layer_name is None:
            print(f"  \u26a0 no layer named {side!r} — skipping")
            continue
        print(f"  replace {layer_name!r} \u2190 {tex_path.name}")
        m.replace_smart_object(layer_name, tex_path)
        replaced.append(layer_name)

    if not replaced:
        raise RuntimeError(
            f"No smart objects replaced. Requested {list(local_textures)} "
            f"but PSD has {available}"
        )

    print("\n[4/4] Export")
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.export(out_path)
    print(f"  \u2713 {out_path.resolve()} ({out_path.stat().st_size:,} bytes)")
    return out_path


def run_local_fixture() -> Path:
    print("No payload — using local fixtures")
    m = Mockup.open("tests/mockup.psd")
    print("Smart objects:", m.list_smart_objects())
    tex = Path("tests/texture.png")
    if not tex.exists():
        tex = Path("tests/small.png")
    m.replace_smart_object("front", tex)
    out = Path("tests/exported.png")
    m.export(out)
    print(f"Done \u2192 {out}")
    return out


def load_payload(args: argparse.Namespace) -> Optional[dict[str, Any]]:
    if args.payload:
        return json.loads(args.payload)
    if args.file:
        return json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.input and args.input != "-":
        p = Path(args.input)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Payload file not found: {p}")
    if args.input == "-" or (not sys.stdin.isatty() and not args.local):
        raw = sys.stdin.read().strip()
        if raw:
            return json.loads(raw)
    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="mockupgen dynamic runner")
    parser.add_argument("input", nargs="?", help="JSON payload file, or '-' for stdin")
    parser.add_argument("--payload", "-p", help="JSON payload as a string")
    parser.add_argument("--file", "-f", help="JSON payload file")
    parser.add_argument("--output", "-o", help="Override output PNG path")
    parser.add_argument("--work-dir", default=None, help="Directory for downloads")
    parser.add_argument("--local", action="store_true", help="Local fixture test")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(argv)
    setup_logging(args.log_level)

    if args.local:
        run_local_fixture()
        return 0

    payload = load_payload(args)
    if payload is None:
        run_local_fixture()
        return 0

    if args.output:
        payload["output"] = args.output

    if args.work_dir:
        run_payload(payload, work_dir=Path(args.work_dir))
    else:
        with tempfile.TemporaryDirectory(prefix="mockupgen_") as tmp:
            out = payload.get("output") or "tests/exported.png"
            payload["output"] = (
                str(Path(out).resolve()) if not Path(out).is_absolute() else out
            )
            run_payload(payload, work_dir=Path(tmp))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
