"""Generate a second independent native P&ID revision scenario."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fitz

from delta_chat.config import project_root
from scripts.make_synthetic_pid_pair import (
    PAGE_H,
    PAGE_W,
    DrawSpec,
    _draw_drawing,
    _extract_text,
    _sha256,
)


def _write_pdf(path: Path, spec: DrawSpec) -> None:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _draw_drawing(page, spec)
    doc.save(str(path))
    doc.close()


def main(seed: int = 84, out_dir: str | Path | None = None) -> dict[str, Any]:
    out = Path(out_dir) if out_dir else project_root() / "data" / "samples" / "synthetic_secondary"
    out.mkdir(parents=True, exist_ok=True)
    shared: dict[str, Any] = {
        "equipment": "27-KA-902",
        "pit": "27-PIT-1001",
        "pt": "27-PT-1002",
        "motor": "Motor: 315 kW",
        "service": "Service: Export Gas",
        "line_tag_in": '6"-PG-2001-B1',
        "title": "SYNTHETIC EXPORT GAS COMPRESSOR P&ID",
        "document_id": "DOC-SYN-EXPORT",
        "sheet": "S2",
        "note_10": "NOTE 10: Export package datasheet",
        "note_11": "NOTE 11: Trip logic per cause and effect",
        "valve": "HV-301",
    }
    spec_a = DrawSpec(
        rev="A",
        duty="Duty: 8000 Nm3/h",
        hh="HH 180",
        h_alarm="H 170",
        line_tag_out='6"-PG-2002-B1',
        note_12=None,
        pt_center=(720.0, 300.0),
        branch=False,
        **shared,
    )
    spec_b = DrawSpec(
        rev="B",
        duty="Duty: 8500 Nm3/h",
        hh="HH 185",
        h_alarm="H 170",
        line_tag_out='6"-PG-2002-B1',
        note_12="NOTE 20: Added anti-surge permissive",
        pt_center=(790.0, 300.0),
        branch=False,
        **shared,
    )
    path_a = out / "export_rev_a.pdf"
    path_b = out / "export_rev_b.pdf"
    _write_pdf(path_a, spec_a)
    _write_pdf(path_b, spec_b)

    text_a = _extract_text(path_a)
    text_b = _extract_text(path_b)
    checks = [
        ("HH 180" in text_a and "HH 180" not in text_b, "secondary HH old value"),
        ("HH 185" in text_b and "HH 185" not in text_a, "secondary HH new value"),
        ("Duty: 8000" in text_a and "Duty: 8000" not in text_b, "secondary old duty"),
        ("Duty: 8500" in text_b and "Duty: 8500" not in text_a, "secondary new duty"),
        ("NOTE 20:" not in text_a and text_b.count("NOTE 20:") == 1, "secondary note"),
        (
            text_a.count("27-KA-902") == 1 and text_b.count("27-KA-902") == 1,
            "secondary equipment anchor",
        ),
    ]
    failures = [label for ok, label in checks if not ok]
    if failures:
        raise AssertionError("Secondary fixture integrity failed: " + ", ".join(failures))

    changes = [
        {
            "change_type": "modified",
            "entity_type": "table_cell",
            "before": "HH 180",
            "after": "HH 185",
            "page": 1,
            "bbox": [250 / PAGE_W, 238 / PAGE_H, 320 / PAGE_W, 255 / PAGE_H],
            "identifiers": ["27-PIT-1001"],
            "label": "export_setpoint_hh",
        },
        {
            "change_type": "modified",
            "entity_type": "table_cell",
            "before": "Duty: 8000 Nm3/h",
            "after": "Duty: 8500 Nm3/h",
            "page": 1,
            "bbox": [90 / PAGE_W, 668 / PAGE_H, 250 / PAGE_W, 688 / PAGE_H],
            "label": "export_duty_value",
        },
        {
            "change_type": "added",
            "entity_type": "note",
            "before": None,
            "after": "NOTE 20: Added anti-surge permissive",
            "page": 1,
            "bbox": [400 / PAGE_W, 730 / PAGE_H, 700 / PAGE_W, 750 / PAGE_H],
            "label": "export_note_20",
        },
        {
            "change_type": "moved",
            "entity_type": "instrument_tag",
            "before": "27-PT-1002",
            "after": "27-PT-1002",
            "page": 1,
            "bbox": [768 / PAGE_W, 278 / PAGE_H, 830 / PAGE_W, 320 / PAGE_H],
            "label": "export_pt_moved",
        },
    ]
    payload: dict[str, Any] = {
        "seed": seed,
        "pid_a": "PID-SYN2-A",
        "pid_b": "PID-SYN2-B",
        "underlying_document_id": "DOC-SYN-EXPORT",
        "sha256_a": _sha256(path_a),
        "sha256_b": _sha256(path_b),
        "controlled_changes": changes,
        "unchanged_anchors": [
            {
                "change_type": "unchanged",
                "entity_type": "equipment_tag",
                "before": "27-KA-902",
                "after": "27-KA-902",
                "page": 1,
                "label": "secondary_equipment_unchanged",
            }
        ],
        "notes": [
            "Independent export-gas fixture with different document, equipment, tags, and values.",
            "Rev A and Rev B are drawn independently.",
        ],
    }
    gt_path = out / "ground_truth.json"
    gt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {path_a}")
    print(f"Wrote {path_b}")
    print(f"Wrote {gt_path}")
    return payload


if __name__ == "__main__":
    main()
