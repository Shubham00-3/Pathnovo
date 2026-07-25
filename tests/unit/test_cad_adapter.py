"""CAD (DXF/DWG) adapter: detection, canonical mapping, and the DWG seam."""

from __future__ import annotations

import json

import pytest

from delta_chat.config import load_config, project_root
from delta_chat.errors import UnsupportedFormatError
from delta_chat.ingest import ADAPTERS, ingest_document
from delta_chat.ingest.detector import detect_format
from delta_chat.pid.local_registry import LocalRegistryResolver

pytest.importorskip("ezdxf", reason="CAD adapter requires ezdxf")

SAMPLE_DIR = project_root() / "data" / "samples" / "synthetic_cad"


@pytest.fixture(scope="module")
def cad_samples():
    if not (SAMPLE_DIR / "booster_rev_a.dxf").exists():
        from scripts.make_cad_pair import main

        main()
    return SAMPLE_DIR


def test_detector_routes_dxf_to_the_cad_adapter(cad_samples):
    signals = detect_format(cad_samples / "booster_rev_a.dxf", load_config())

    assert signals["adapter"] == "dxf"
    assert signals["format_family"] == "dxf"


def test_detector_routes_dwg_magic_to_the_cad_adapter(tmp_path):
    """DWG reaches the same adapter, which then requires a converter."""
    dwg = tmp_path / "drawing.dwg"
    dwg.write_bytes(b"AC1032" + b"\0" * 64)

    signals = detect_format(dwg, load_config())

    assert signals["adapter"] == "dxf"
    assert signals["format_family"] == "dwg"


def test_dwg_without_a_converter_fails_with_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DWG_CONVERTER_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    dwg = tmp_path / "drawing.dwg"
    dwg.write_bytes(b"AC1032" + b"\0" * 64)

    from delta_chat.ingest.dxf import DxfAdapter
    from delta_chat.pid.models import ResolvedDocument

    resolved = ResolvedDocument(
        pid="PID-DWG",
        underlying_document_id="DOC-DWG",
        revision_label="A",
        media_type="image/vnd.dwg",
        source_uri=str(dwg),
        local_path=str(dwg),
        byte_size=dwg.stat().st_size,
        sha256="0" * 64,
    )

    with pytest.raises(UnsupportedFormatError) as excinfo:
        DxfAdapter().ingest(resolved, out_dir=tmp_path / "out", config=load_config())

    details = excinfo.value.details
    assert details["missing_dependency"]
    assert "converter_path" in details["suggested_configuration"]


def test_dxf_ingests_into_the_same_canonical_shape(cad_samples, tmp_path):
    """The delta engine must not be able to tell CAD from PDF."""
    cfg = load_config()
    resolver = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))
    resolved = resolver.resolve("PID-CAD-A")

    doc, signals = ingest_document(resolved, out_dir=tmp_path, config=cfg)

    assert doc.adapter_name == "dxf"
    assert doc.source_format == "dxf"
    assert len(doc.pages) == 1

    page = doc.pages[0]
    assert page.elements
    # Coordinates are exact vectors: no recognition step, so full confidence.
    assert all(el.extraction_confidence == 1.0 for el in page.elements)
    # Normalized top-left space, same as every other adapter.
    for el in page.elements:
        assert len(el.bbox) == 4
        assert all(0.0 <= v <= 1.0 for v in el.bbox)
        assert el.bbox[0] <= el.bbox[2] and el.bbox[1] <= el.bbox[3]


def test_dxf_recovers_tags_and_layers(cad_samples, tmp_path):
    cfg = load_config()
    resolver = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))
    doc, _ = ingest_document(resolver.resolve("PID-CAD-A"), out_dir=tmp_path, config=cfg)

    identifiers = {i for page in doc.pages for el in page.elements for i in el.identifiers}
    assert "26-KA-903" in identifiers
    assert "26-PIT-9080" in identifiers

    layers = {(el.attributes or {}).get("layer") for page in doc.pages for el in page.elements}
    assert "INSTRUMENT" in layers
    assert doc.metadata["cad_engine"] == "ezdxf"


def test_dxf_produces_a_render_for_downstream_stages(cad_samples, tmp_path):
    """Markup and the UI need a page image even though DXF has no pages."""
    from pathlib import Path

    cfg = load_config()
    resolver = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))
    doc, _ = ingest_document(resolver.resolve("PID-CAD-B"), out_dir=tmp_path, config=cfg)

    render = doc.pages[0].render_path
    assert render and Path(render).exists()


def test_cad_adapter_is_registered_behind_the_common_seam():
    assert "dxf" in ADAPTERS
    adapter = ADAPTERS["dxf"]
    assert hasattr(adapter, "ingest") and hasattr(adapter, "supports")


def test_ground_truth_matches_the_generated_samples(cad_samples):
    gt = json.loads((cad_samples / "ground_truth.json").read_text(encoding="utf-8"))
    assert gt["pid_a"] == "PID-CAD-A"
    assert len(gt["controlled_changes"]) == 6
    assert {c["change_type"] for c in gt["controlled_changes"]} == {
        "modified",
        "added",
        "removed",
        "moved",
    }
