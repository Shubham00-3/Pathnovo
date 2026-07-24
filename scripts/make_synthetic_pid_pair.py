"""Deterministic A3 P&ID-like synthetic revision pair generator.

Rev A and Rev B are drawn independently from a shared parameterized
specification. Obsolete content is never left as hidden PDF text under
white rectangles.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from delta_chat.config import project_root

PAGE_W, PAGE_H = 1191.0, 842.0


@dataclass
class DrawSpec:
    rev: str
    duty: str
    hh: str
    h_alarm: str
    line_tag_out: str | None
    note_12: str | None
    pt_center: tuple[float, float]
    branch: bool
    equipment: str = "26-KA-901"
    pit: str = "26-PIT-9062"
    pt: str = "26-PT-9070"
    motor: str = "Motor: 250 kW"
    service: str = "Service: Gas Lift"
    line_tag_in: str = '4"-PG-1001-A1'
    note_10: str = "NOTE 10: See package datasheet"
    note_11: str = "NOTE 11: Relief design per API"
    valve: str = "HV-101"
    extra: dict[str, Any] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _draw_drawing(page: fitz.Page, spec: DrawSpec) -> None:
    # border
    page.draw_rect(fitz.Rect(30, 30, PAGE_W - 30, PAGE_H - 30), color=(0, 0, 0), width=1.5)

    # title block
    page.draw_rect(fitz.Rect(PAGE_W - 320, PAGE_H - 140, PAGE_W - 40, PAGE_H - 40), width=1)
    page.insert_text(
        (PAGE_W - 300, PAGE_H - 120), "SYNTHETIC GAS LIFT COMPRESSOR P&ID", fontsize=10
    )
    page.insert_text((PAGE_W - 300, PAGE_H - 100), "DOC: DOC-SYN-LIFT", fontsize=9)
    page.insert_text((PAGE_W - 300, PAGE_H - 80), f"REV: {spec.rev}", fontsize=9)
    page.insert_text((PAGE_W - 300, PAGE_H - 60), "SHEET: S1", fontsize=9)

    # grid labels: columns 1..8 along top, rows A..F along left (honest approximate grid)
    for i in range(8):
        page.insert_text((80 + i * 130, 50), str(i + 1), fontsize=9, color=(0.3, 0.3, 0.3))
    for i, letter in enumerate("ABCDEF"):
        page.insert_text((40, 90 + i * 110), letter, fontsize=9, color=(0.3, 0.3, 0.3))

    # main equipment
    page.draw_oval(fitz.Rect(480, 300, 620, 440), width=1.5)
    page.insert_text((510, 360), spec.equipment, fontsize=12)
    page.insert_text((500, 380), "COMPRESSOR", fontsize=9)

    # process lines
    page.draw_line(fitz.Point(120, 370), fitz.Point(480, 370), width=1.2)
    page.draw_line(fitz.Point(620, 370), fitz.Point(980, 370), width=1.2)
    page.insert_text((200, 355), spec.line_tag_in, fontsize=9)
    if spec.line_tag_out:
        page.insert_text((700, 355), spec.line_tag_out, fontsize=9)

    # instruments
    page.draw_circle(fitz.Point(300, 300), 22, width=1)
    page.insert_text((278, 304), spec.pit, fontsize=8)
    page.insert_text((250, 250), spec.hh, fontsize=9)
    page.insert_text((250, 265), spec.h_alarm, fontsize=9)

    cx, cy = spec.pt_center
    page.draw_circle(fitz.Point(cx, cy), 22, width=1)
    page.insert_text((cx - 22, cy + 4), spec.pt, fontsize=8)

    # duty table
    page.draw_rect(fitz.Rect(80, 620, 360, 720), width=1)
    page.insert_text((90, 640), "DUTY TABLE", fontsize=9)
    page.insert_text((90, 660), spec.service, fontsize=9)
    page.insert_text((90, 680), spec.duty, fontsize=9)
    page.insert_text((90, 700), spec.motor, fontsize=9)

    # notes
    page.insert_text((400, 700), spec.note_10, fontsize=8)
    page.insert_text((400, 720), spec.note_11, fontsize=8)
    if spec.note_12:
        page.insert_text((400, 740), spec.note_12, fontsize=8)

    # valve
    page.draw_line(fitz.Point(400, 360), fitz.Point(420, 380), width=1)
    page.draw_line(fitz.Point(420, 380), fitz.Point(400, 400), width=1)
    page.draw_line(fitz.Point(400, 360), fitz.Point(400, 400), width=1)
    page.insert_text((390, 415), spec.valve, fontsize=8)

    # optional branch + HV-205
    if spec.branch:
        page.draw_line(fitz.Point(850, 370), fitz.Point(850, 470), width=1.2)
        page.draw_line(fitz.Point(850, 470), fitz.Point(920, 470), width=1.2)
        page.draw_line(fitz.Point(840, 450), fitz.Point(860, 470), width=1)
        page.draw_line(fitz.Point(860, 470), fitz.Point(840, 490), width=1)
        page.insert_text((830, 505), "HV-205", fontsize=8)


def _extract_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text("text") or "" for page in doc)
    finally:
        doc.close()


def _assert_fixture_integrity(path_a: Path, path_b: Path) -> None:
    ta = _extract_text(path_a)
    tb = _extract_text(path_b)

    def count(hay: str, needle: str) -> int:
        return hay.count(needle)

    checks = [
        (count(ta, "HH 245") == 1, "Rev A must contain HH 245 once"),
        (count(ta, "HH 250") == 0, "Rev A must not contain HH 250"),
        (count(tb, "HH 250") == 1, "Rev B must contain HH 250 once"),
        (count(tb, "HH 245") == 0, "Rev B must not contain obsolete HH 245"),
        (count(ta, "Duty: 12000 Nm3/h") == 1, "Rev A duty once"),
        (count(tb, "Duty: 12500 Nm3/h") == 1, "Rev B duty once"),
        (count(tb, "Duty: 12000") == 0, "Rev B must not contain old duty"),
        (count(ta, '4"-PG-1002-A1') == 1, "Rev A has outbound line tag"),
        (count(tb, '4"-PG-1002-A1') == 0, "Rev B removed outbound line tag"),
        (count(tb, "NOTE 12:") == 1, "Rev B has NOTE 12 once"),
        (count(ta, "NOTE 12:") == 0, "Rev A has no NOTE 12"),
        (count(ta, "26-KA-901") == 1 and count(tb, "26-KA-901") == 1, "equipment unchanged"),
        (count(ta, "26-PIT-9062") == 1 and count(tb, "26-PIT-9062") == 1, "PIT present once each"),
        (count(tb, "HV-205") == 1, "branch valve present in B"),
        (count(ta, "HV-205") == 0, "branch valve absent in A"),
        ("HH HH" not in tb and "Duty: Duty:" not in tb, "no merged overlay garbage in B"),
    ]
    failures = [msg for ok, msg in checks if not ok]
    if failures:
        raise AssertionError("Synthetic fixture integrity failed:\n- " + "\n- ".join(failures))


def main(seed: int = 42, out_dir: str | Path | None = None) -> dict:
    random.seed(seed)
    root = project_root()
    out = Path(out_dir) if out_dir else root / "data" / "samples" / "synthetic_native"
    out.mkdir(parents=True, exist_ok=True)

    spec_a = DrawSpec(
        rev="A",
        duty="Duty: 12000 Nm3/h",
        hh="HH 245",
        h_alarm="H 230",
        line_tag_out='4"-PG-1002-A1',
        note_12=None,
        pt_center=(760.0, 300.0),
        branch=False,
    )
    spec_b = DrawSpec(
        rev="B",
        duty="Duty: 12500 Nm3/h",
        hh="HH 250",
        h_alarm="H 230",
        line_tag_out=None,  # removed
        note_12="NOTE 12: Added for startup interlock",
        pt_center=(860.0, 300.0),  # moved
        branch=True,
    )

    path_a = out / "lift_rev_a.pdf"
    path_b = out / "lift_rev_b.pdf"

    doc_a = fitz.open()
    page_a = doc_a.new_page(width=PAGE_W, height=PAGE_H)
    _draw_drawing(page_a, spec_a)
    doc_a.save(str(path_a))
    doc_a.close()

    doc_b = fitz.open()
    page_b = doc_b.new_page(width=PAGE_W, height=PAGE_H)
    _draw_drawing(page_b, spec_b)
    doc_b.save(str(path_b))
    doc_b.close()

    _assert_fixture_integrity(path_a, path_b)

    changes = [
        {
            "change_type": "modified",
            "entity_type": "table_cell",
            "before": "HH 245",
            "after": "HH 250",
            "page": 1,
            "bbox": [250 / PAGE_W, 238 / PAGE_H, 320 / PAGE_W, 255 / PAGE_H],
            "identifiers": ["26-PIT-9062"],
            "label": "setpoint_hh",
        },
        {
            "change_type": "modified",
            "entity_type": "table_cell",
            "before": "Duty: 12000 Nm3/h",
            "after": "Duty: 12500 Nm3/h",
            "page": 1,
            "bbox": [90 / PAGE_W, 668 / PAGE_H, 250 / PAGE_W, 688 / PAGE_H],
            "label": "duty_value",
        },
        {
            "change_type": "added",
            "entity_type": "note",
            "before": None,
            "after": "NOTE 12: Added for startup interlock",
            "page": 1,
            "bbox": [400 / PAGE_W, 730 / PAGE_H, 700 / PAGE_W, 750 / PAGE_H],
            "label": "note_12",
        },
        {
            "change_type": "removed",
            "entity_type": "line_tag",
            "before": '4"-PG-1002-A1',
            "after": None,
            "page": 1,
            "bbox": [700 / PAGE_W, 345 / PAGE_H, 820 / PAGE_W, 365 / PAGE_H],
            "label": "line_tag_removed",
        },
        {
            "change_type": "moved",
            "entity_type": "instrument_tag",
            "before": "26-PT-9070",
            "after": "26-PT-9070",
            "page": 1,
            "bbox": [838 / PAGE_W, 278 / PAGE_H, 900 / PAGE_W, 320 / PAGE_H],
            "label": "pt_moved",
        },
        {
            "change_type": "added",
            "entity_type": "geometry_region",
            "before": None,
            "after": "branch with HV-205",
            "page": 1,
            "bbox": [830 / PAGE_W, 360 / PAGE_H, 930 / PAGE_W, 520 / PAGE_H],
            "label": "geometry_branch",
        },
    ]

    payload = {
        "seed": seed,
        "pid_a": "PID-SYN-A",
        "pid_b": "PID-SYN-B",
        "underlying_document_id": "DOC-SYN-LIFT",
        "sha256_a": _sha256(path_a),
        "sha256_b": _sha256(path_b),
        "controlled_changes": changes,
        "unchanged_anchors": [
            {
                "change_type": "unchanged",
                "entity_type": "equipment_tag",
                "before": "26-KA-901",
                "after": "26-KA-901",
                "page": 1,
                "label": "equipment_unchanged",
            }
        ],
        "notes": [
            "Rev A and Rev B drawn independently (no white-out of PDF text).",
            "Motor kW text does not change; motor vendor is not present.",
        ],
        "grid_convention": {
            "columns": "1-8 left-to-right",
            "rows": "A-F top-to-bottom",
            "origin": "top-left",
            "approximate": True,
        },
    }
    gt_path = out / "ground_truth.json"
    gt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {path_a}")
    print(f"Wrote {path_b}")
    print(f"Wrote {gt_path}")
    print(f"SHA A={payload['sha256_a'][:12]} B={payload['sha256_b'][:12]}")
    return payload


if __name__ == "__main__":
    main()
