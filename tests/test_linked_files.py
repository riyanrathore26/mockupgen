"""Tests for Phase 3 — linked-file parsing and smart-object extract.

Place a sample PSD at tests/fixtures/tshirt.psd (or set SAMPLE_PSD env)
to run the integration checks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SAMPLE = Path(os.environ.get("SAMPLE_PSD", "tests/fixtures/tshirt.psd"))


@pytest.mark.skipif(not SAMPLE.exists(), reason="Sample PSD not present")
def test_list_smart_objects():
    from mockupgen import Mockup, setup_logging

    setup_logging("DEBUG")
    m = Mockup.open(SAMPLE)
    names = m.list_smart_objects()
    assert "front" in names or any("front" in n.lower() for n in names)


@pytest.mark.skipif(not SAMPLE.exists(), reason="Sample PSD not present")
def test_list_linked_files():
    from mockupgen import Mockup

    m = Mockup.open(SAMPLE)
    files = m.list_linked_files()
    assert len(files) >= 1
    assert all("uuid" in f and "filename" in f for f in files)


@pytest.mark.skipif(not SAMPLE.exists(), reason="Sample PSD not present")
def test_extract_front(tmp_path):
    from mockupgen import Mockup

    m = Mockup.open(SAMPLE)
    dest = tmp_path / "front.bin"
    data = m.extract_smart_object("front", dest)
    assert len(data) > 100
    assert dest.exists()
    assert dest.stat().st_size == len(data)
    # Should start with PSD or image magic
    assert data[:4] in (b"8BPS", b"\x89PNG") or data[:3] == b"\xff\xd8\xff"
