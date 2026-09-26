"""Basic tests for File Header parsing."""

from pathlib import Path

from mockupgen.psd.document import PSDDocument
from mockupgen.psd.header import FileHeader
from mockupgen.binary.reader import BinaryReader

# Path to the sample t-shirt PSD (adjust if you move the file)
SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "tshirt.psd"


def test_header_from_sample():
    if not SAMPLE.exists():
        # Skip if sample is not present yet
        return

    doc = PSDDocument.open(SAMPLE)
    assert doc.width == 896
    assert doc.height == 1200
    assert doc.depth == 8
    assert doc.color_mode == 3  # RGB
    assert doc.header.version == 1


def test_header_roundtrip():
    h = FileHeader(
        channels=3,
        height=1200,
        width=896,
        depth=8,
        color_mode=3,
    )
    from mockupgen.binary.writer import BinaryWriter

    w = BinaryWriter()
    h.write(w)
    data = w.getvalue()
    assert len(data) == 26

    reader = BinaryReader(data)
    h2 = FileHeader.read(reader)
    assert h2.width == 896
    assert h2.height == 1200
    assert h2.depth == 8
    assert h2.color_mode == 3
