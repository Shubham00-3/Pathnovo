"""Deterministic DXF revision pair generator (third format, end-to-end).

Rev A and Rev B are authored independently from one parameterized spec, exactly
like the PDF generators -- no editing of a saved file, so no stale entities hide
under the changes. The controlled edits mirror the PDF pairs (setpoint, duty,
removed line tag, moved transmitter, added note, added branch) so delta quality
is comparable across formats.

Provenance: fully synthetic. No customer drawing is used or derived from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ezdxf

from delta_chat.config import project_root

# Drawing extents in millimetres (ISO A3 landscape).
EXT_W, EXT_H = 420.0, 297.0


@dataclass
class CadSpec:
    rev: str
    duty: str
    hh: str
    line_tag_out: str | None
    note_12: str | None
    pt_center: tuple[float, float]
    branch: bool
    equipment: str = "26-KA-903"
    pit: str = "26-PIT-9080"
    pt: str = "26-PT-9085"
    line_tag_in: str = '6"-PG-2001-A1'
    note_10: str = "NOTE 10: CAD source of record"
    document_id: str = "DOC-CAD-BOOSTER"
    title: str = "SYNTHETIC BOOSTER COMPRESSOR P&ID (CAD)"


SPEC_A = CadSpec(
    rev="A",
    duty="Duty: 8000 Nm3/h",
    hh="HH 180",
    line_tag_out='6"-PG-2002-A1',
    note_12=None,
    pt_center=(300.0, 190.0),
    branch=False,
)

SPEC_B = CadSpec(
    rev="B",
    duty="Duty: 8600 Nm3/h",
    hh="HH 195",
    line_tag_out=None,
    note_12="NOTE 12: Added for surge control",
    pt_center=(300.0, 168.0),
    branch=True,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm_bbox(x0: float, y0: float, x1: float, y1: float) -> list[float]:
    """DXF mm coordinates -> canonical normalized top-left box."""
    return [
        min(x0, x1) / EXT_W,
        1.0 - max(y0, y1) / EXT_H,
        max(x0, x1) / EXT_W,
        1.0 - min(y0, y1) / EXT_H,
    ]


def _build(spec: CadSpec, out_path: Path) -> None:
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    for layer, color in (
        ("BORDER", 8),
        ("EQUIPMENT", 3),
        ("PIPING", 5),
        ("INSTRUMENT", 1),
        ("TEXT", 7),
        ("TITLE", 7),
    ):
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)

    def text(
        content: str, pos: tuple[float, float], height: float = 4.0, layer: str = "TEXT"
    ) -> None:
        msp.add_text(content, height=height, dxfattribs={"layer": layer}).set_placement(pos)

    # Anchor geometry: border and title block are unchanged across revisions and
    # give the matcher stable reference points.
    msp.add_lwpolyline(
        [(10, 10), (EXT_W - 10, 10), (EXT_W - 10, EXT_H - 10), (10, EXT_H - 10), (10, 10)],
        dxfattribs={"layer": "BORDER"},
    )
    msp.add_lwpolyline(
        [
            (EXT_W - 130, 15),
            (EXT_W - 15, 15),
            (EXT_W - 15, 60),
            (EXT_W - 130, 60),
            (EXT_W - 130, 15),
        ],
        dxfattribs={"layer": "BORDER"},
    )
    text(spec.title, (EXT_W - 126, 50), height=3.4, layer="TITLE")
    text(f"DOC: {spec.document_id}", (EXT_W - 126, 42), height=3.0, layer="TITLE")
    text(f"REV: {spec.rev}", (EXT_W - 126, 34), height=3.0, layer="TITLE")
    text("SHEET: S1", (EXT_W - 126, 26), height=3.0, layer="TITLE")

    # Compressor body.
    msp.add_lwpolyline(
        [(70, 150), (150, 150), (150, 210), (70, 210), (70, 150)],
        dxfattribs={"layer": "EQUIPMENT"},
    )
    msp.add_circle((110, 180), radius=22, dxfattribs={"layer": "EQUIPMENT"})
    text(spec.equipment, (78, 216), height=5.0, layer="EQUIPMENT")
    text("BOOSTER COMPRESSOR", (78, 142), height=3.6)

    # Process lines.
    msp.add_line((25, 180), (70, 180), dxfattribs={"layer": "PIPING"})
    text(spec.line_tag_in, (26, 185), height=3.6, layer="PIPING")
    msp.add_line((150, 180), (250, 180), dxfattribs={"layer": "PIPING"})
    if spec.line_tag_out:
        text(spec.line_tag_out, (170, 185), height=3.6, layer="PIPING")

    # Instrument on the discharge header.
    msp.add_circle((215, 215), radius=9, dxfattribs={"layer": "INSTRUMENT"})
    msp.add_line((215, 180), (215, 206), dxfattribs={"layer": "INSTRUMENT"})
    text(spec.pit, (196, 228), height=3.6, layer="INSTRUMENT")
    text(spec.hh, (200, 236), height=3.6, layer="INSTRUMENT")

    # Transmitter whose position differs between revisions.
    px, py = spec.pt_center
    msp.add_circle((px, py), radius=9, dxfattribs={"layer": "INSTRUMENT"})
    text(spec.pt, (px - 19, py + 13), height=3.6, layer="INSTRUMENT")

    # Branch present only in Rev B.
    if spec.branch:
        msp.add_line((250, 180), (250, 120), dxfattribs={"layer": "PIPING"})
        msp.add_lwpolyline(
            [(244, 112), (256, 112), (256, 120), (244, 120), (244, 112)],
            dxfattribs={"layer": "PIPING"},
        )
        text("HV-305", (258, 114), height=3.6, layer="PIPING")

    # Duty table and notes.
    text("DUTY TABLE", (30, 90), height=3.8)
    text(spec.duty, (30, 82), height=3.6)
    text("Service: Booster", (30, 74), height=3.6)
    text("Motor: 400 kW", (30, 66), height=3.6)
    text(spec.note_10, (150, 90), height=3.6)
    text("NOTE 11: Relief design per API", (150, 82), height=3.6)
    if spec.note_12:
        text(spec.note_12, (150, 74), height=3.6)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(out_path))


def _ground_truth(path_a: Path, path_b: Path) -> dict[str, Any]:
    return {
        "seed": 42,
        "pid_a": "PID-CAD-A",
        "pid_b": "PID-CAD-B",
        "underlying_document_id": "DOC-CAD-BOOSTER",
        "sha256_a": _sha256(path_a),
        "sha256_b": _sha256(path_b),
        "controlled_changes": [
            {
                "change_type": "modified",
                "entity_type": "table_cell",
                "before": "HH 180",
                "after": "HH 195",
                "page": 1,
                "bbox": _norm_bbox(200, 236, 232, 240),
                "identifiers": ["26-PIT-9080"],
                "label": "setpoint_hh",
            },
            {
                "change_type": "modified",
                "entity_type": "table_cell",
                "before": "Duty: 8000 Nm3/h",
                "after": "Duty: 8600 Nm3/h",
                "page": 1,
                "bbox": _norm_bbox(30, 82, 96, 86),
                "label": "duty_value",
            },
            {
                "change_type": "added",
                "entity_type": "note",
                "before": None,
                "after": "NOTE 12: Added for surge control",
                "page": 1,
                "bbox": _norm_bbox(150, 74, 258, 78),
                "label": "note_12",
            },
            {
                "change_type": "removed",
                "entity_type": "line_tag",
                "before": '6"-PG-2002-A1',
                "after": None,
                "page": 1,
                "bbox": _norm_bbox(170, 185, 226, 189),
                "label": "line_tag_removed",
            },
            {
                "change_type": "moved",
                "entity_type": "instrument_tag",
                "before": "26-PT-9085",
                "after": "26-PT-9085",
                "page": 1,
                "bbox": _norm_bbox(281, 181, 319, 203),
                "label": "pt_moved",
            },
            {
                "change_type": "added",
                "entity_type": "geometry_region",
                "before": None,
                "after": "branch with HV-305",
                "page": 1,
                "bbox": _norm_bbox(244, 112, 280, 180),
                "label": "geometry_branch",
            },
        ],
        "unchanged_anchors": [
            {
                "change_type": "unchanged",
                "entity_type": "equipment_tag",
                "before": "26-KA-903",
                "after": "26-KA-903",
                "page": 1,
                "label": "equipment_unchanged",
            }
        ],
        "notes": [
            "Rev A and Rev B authored independently from one spec; no edited-file residue.",
            "DXF R2010 ASCII. Coordinates are exact vectors, so no OCR error is present.",
            "Motor kW and service text are identical across revisions (negative controls).",
        ],
        "grid_convention": {
            "columns": "1-8 left-to-right",
            "rows": "A-F top-to-bottom",
            "origin": "top-left",
            "approximate": True,
        },
        "location_tolerance": 0.08,
        "provenance": "Synthetic, generated by scripts/make_cad_pair.py",
    }


def main() -> dict[str, str]:
    root = project_root()
    out_dir = root / "data" / "samples" / "synthetic_cad"
    path_a = out_dir / "booster_rev_a.dxf"
    path_b = out_dir / "booster_rev_b.dxf"
    _build(SPEC_A, path_a)
    _build(SPEC_B, path_b)

    gt_path = out_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(_ground_truth(path_a, path_b), indent=2) + "\n", encoding="utf-8")

    registry_path = root / "data" / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["PID-CAD-A"] = {
        "underlying_document_id": "DOC-CAD-BOOSTER",
        "revision_label": "A",
        "path": "data/samples/synthetic_cad/booster_rev_a.dxf",
        "media_type": "image/vnd.dxf",
        "display_name": "Synthetic Booster CAD Rev A",
    }
    registry["PID-CAD-B"] = {
        "underlying_document_id": "DOC-CAD-BOOSTER",
        "revision_label": "B",
        "path": "data/samples/synthetic_cad/booster_rev_b.dxf",
        "media_type": "image/vnd.dxf",
        "display_name": "Synthetic Booster CAD Rev B",
    }
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {path_a}")
    print(f"Wrote {path_b}")
    print(f"Wrote {gt_path}")
    return {"rev_a": str(path_a), "rev_b": str(path_b), "ground_truth": str(gt_path)}


if __name__ == "__main__":
    main()
