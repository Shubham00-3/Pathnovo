from pathlib import Path

import pytest

from delta_chat.errors import UnsupportedFormatError
from delta_chat.ingest.detector import detect_format
from delta_chat.ingest.dwg import DwgAdapter
from delta_chat.pid.models import ResolvedDocument


def test_detect_pdf_magic(tmp_path: Path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    # may be corrupt for fitz - write minimal valid via fitz if needed
    import fitz

    doc = fitz.open()
    doc.new_page()
    page = doc[0]
    page.insert_text((72, 72), "Hello native text enough characters for density")
    doc.save(str(p))
    doc.close()
    signals = detect_format(p, {"detection": {}})
    assert signals["adapter"] in {"native_pdf", "scanned_pdf"}


def test_dwg_routes_to_the_cad_adapter(tmp_path: Path):
    """DWG now shares the CAD path, which converts to DXF before parsing."""
    p = tmp_path / "x.dwg"
    p.write_bytes(b"AC1027" + b"\x00" * 20)
    signals = detect_format(p)
    assert signals["adapter"] == "dxf"
    assert signals["format_family"] == "dwg"


def test_dwg_visible_failure(tmp_path: Path):
    """The legacy DWG adapter stays reachable via force_adapter and still
    explains what to install rather than failing opaquely."""
    p = tmp_path / "x.dwg"
    p.write_bytes(b"AC1027" + b"\x00" * 20)
    adapter = DwgAdapter()
    resolved = ResolvedDocument(
        pid="PID-DWG",
        underlying_document_id="DOC-DWG",
        revision_label="A",
        source_uri=str(p),
        local_path=str(p),
        byte_size=p.stat().st_size,
        sha256="abc",
    )
    with pytest.raises(UnsupportedFormatError) as ei:
        adapter.ingest(resolved, out_dir=tmp_path / "out", config={})
    err = ei.value
    assert err.details.get("detected_format") == "dwg"
    assert "suggested_configuration" in err.details
