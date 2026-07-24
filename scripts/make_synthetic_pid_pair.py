"""Deterministic A3 P&ID-like synthetic revision pair generator."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import fitz

from delta_chat.config import project_root

# A3 landscape points (approx)
PAGE_W, PAGE_H = 1191, 842


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _draw_base(page: fitz.Page, rev: str) -> None:
    # border
    page.draw_rect(fitz.Rect(30, 30, PAGE_W - 30, PAGE_H - 30), color=(0, 0, 0), width=1.5)
    # title block
    page.draw_rect(fitz.Rect(PAGE_W - 320, PAGE_H - 140, PAGE_W - 40, PAGE_H - 40), width=1)
    page.insert_text((PAGE_W - 300, PAGE_H - 120), "SYNTHETIC GAS LIFT COMPRESSOR P&ID", fontsize=10)
    page.insert_text((PAGE_W - 300, PAGE_H - 100), "DOC: DOC-SYN-LIFT", fontsize=9)
    page.insert_text((PAGE_W - 300, PAGE_H - 80), f"REV: {rev}", fontsize=9)
    page.insert_text((PAGE_W - 300, PAGE_H - 60), "SHEET: S1", fontsize=9)

    # grid labels
    for i, letter in enumerate("ABCDEFGH"):
        page.insert_text((80 + i * 130, 50), letter, fontsize=9, color=(0.3, 0.3, 0.3))
    for i in range(1, 7):
        page.insert_text((40, 80 + i * 110), str(i), fontsize=9, color=(0.3, 0.3, 0.3))

    # main equipment
    page.draw_oval(fitz.Rect(480, 300, 620, 440), width=1.5)
    page.insert_text((510, 360), "26-KA-901", fontsize=12)
    page.insert_text((500, 380), "COMPRESSOR", fontsize=9)

    # process lines
    page.draw_line(fitz.Point(120, 370), fitz.Point(480, 370), width=1.2)
    page.draw_line(fitz.Point(620, 370), fitz.Point(980, 370), width=1.2)
    page.insert_text((200, 355), '4"-PG-1001-A1', fontsize=9)
    page.insert_text((700, 355), '4"-PG-1002-A1', fontsize=9)

    # instruments
    page.draw_circle(fitz.Point(300, 300), 22, width=1)
    page.insert_text((278, 304), "26-PIT-9062", fontsize=8)

    page.draw_circle(fitz.Point(760, 300), 22, width=1)
    page.insert_text((738, 304), "26-PT-9070", fontsize=8)

    # table-like duty block
    page.draw_rect(fitz.Rect(80, 620, 360, 720), width=1)
    page.insert_text((90, 640), "DUTY TABLE", fontsize=9)
    page.insert_text((90, 660), "Service: Gas Lift", fontsize=9)
    page.insert_text((90, 680), "Duty: 12000 Nm3/h", fontsize=9)
    page.insert_text((90, 700), "Motor: 250 kW", fontsize=9)

    # notes
    page.insert_text((400, 700), "NOTE 10: See package datasheet", fontsize=8)
    page.insert_text((400, 720), "NOTE 11: Relief design per API", fontsize=8)

    # valve symbol simple
    page.draw_line(fitz.Point(400, 360), fitz.Point(420, 380), width=1)
    page.draw_line(fitz.Point(420, 380), fitz.Point(400, 400), width=1)
    page.draw_line(fitz.Point(400, 360), fitz.Point(400, 400), width=1)
    page.insert_text((390, 415), "HV-101", fontsize=8)

    # setpoint near PIT
    page.insert_text((250, 250), "HH 245", fontsize=9)
    page.insert_text((250, 265), "H 230", fontsize=9)


def _draw_rev_b_changes(page: fitz.Page) -> list[dict]:
    """Apply controlled changes; return ground truth list."""
    gt: list[dict] = []

    # 1) Modify instrument HH setpoint 245 -> 250
    # Cover old text with white and write new
    page.draw_rect(fitz.Rect(248, 238, 320, 255), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((250, 250), "HH 250", fontsize=9)
    gt.append(
        {
            "change_type": "modified",
            "entity_type": "instrument_tag",
            "before": "HH 245",
            "after": "HH 250",
            "page": 1,
            "bbox": [250 / PAGE_W, 238 / PAGE_H, 320 / PAGE_W, 255 / PAGE_H],
            "identifiers": ["26-PIT-9062"],
            "label": "setpoint_hh",
        }
    )

    # 2) Modify table duty 12000 -> 12500
    page.draw_rect(fitz.Rect(88, 668, 250, 688), color=(1, 1, 1), fill=(1, 1, 1))
    page.insert_text((90, 680), "Duty: 12500 Nm3/h", fontsize=9)
    gt.append(
        {
            "change_type": "modified",
            "entity_type": "table_cell",
            "before": "Duty: 12000 Nm3/h",
            "after": "Duty: 12500 Nm3/h",
            "page": 1,
            "bbox": [90 / PAGE_W, 668 / PAGE_H, 250 / PAGE_W, 688 / PAGE_H],
            "label": "duty_value",
        }
    )

    # 3) Add a note
    page.insert_text((400, 740), "NOTE 12: Added for startup interlock", fontsize=8)
    gt.append(
        {
            "change_type": "added",
            "entity_type": "note",
            "before": None,
            "after": "NOTE 12: Added for startup interlock",
            "page": 1,
            "bbox": [400 / PAGE_W, 730 / PAGE_H, 700 / PAGE_W, 750 / PAGE_H],
            "label": "note_12",
        }
    )

    # 4) Remove line tag 4"-PG-1002-A1 (cover it)
    page.draw_rect(fitz.Rect(690, 340, 820, 365), color=(1, 1, 1), fill=(1, 1, 1))
    gt.append(
        {
            "change_type": "removed",
            "entity_type": "line_tag",
            "before": '4"-PG-1002-A1',
            "after": None,
            "page": 1,
            "bbox": [700 / PAGE_W, 345 / PAGE_H, 820 / PAGE_W, 365 / PAGE_H],
            "label": "line_tag_removed",
        }
    )

    # 5) Move instrument bubble PT-9070 right without changing text
    page.draw_rect(fitz.Rect(730, 270, 800, 335), color=(1, 1, 1), fill=(1, 1, 1))
    page.draw_circle(fitz.Point(860, 300), 22, width=1)
    page.insert_text((838, 304), "26-PT-9070", fontsize=8)
    gt.append(
        {
            "change_type": "moved",
            "entity_type": "instrument_tag",
            "before": "26-PT-9070",
            "after": "26-PT-9070",
            "page": 1,
            "bbox": [838 / PAGE_W, 278 / PAGE_H, 900 / PAGE_W, 320 / PAGE_H],
            "label": "pt_moved",
        }
    )

    # 6) Add geometry branch + valve
    page.draw_line(fitz.Point(850, 370), fitz.Point(850, 470), width=1.2)
    page.draw_line(fitz.Point(850, 470), fitz.Point(920, 470), width=1.2)
    page.draw_line(fitz.Point(840, 450), fitz.Point(860, 470), width=1)
    page.draw_line(fitz.Point(860, 470), fitz.Point(840, 490), width=1)
    page.insert_text((830, 505), "HV-205", fontsize=8)
    gt.append(
        {
            "change_type": "added",
            "entity_type": "geometry_region",
            "before": None,
            "after": "branch with HV-205",
            "page": 1,
            "bbox": [830 / PAGE_W, 360 / PAGE_H, 930 / PAGE_W, 520 / PAGE_H],
            "label": "geometry_branch",
        }
    )

    # 7 is implicit: leave several items unchanged (compressor, PIT text, motor, note 10/11)

    # Also write explicit unchanged anchors for eval
    gt.append(
        {
            "change_type": "unchanged",
            "entity_type": "equipment_tag",
            "before": "26-KA-901",
            "after": "26-KA-901",
            "page": 1,
            "label": "equipment_unchanged",
        }
    )
    return gt


def main(seed: int = 42, out_dir: str | Path | None = None) -> dict:
    random.seed(seed)
    root = project_root()
    out = Path(out_dir) if out_dir else root / "data" / "samples" / "synthetic_native"
    out.mkdir(parents=True, exist_ok=True)

    # Rev A
    doc_a = fitz.open()
    page_a = doc_a.new_page(width=PAGE_W, height=PAGE_H)
    _draw_base(page_a, "A")
    path_a = out / "lift_rev_a.pdf"
    doc_a.save(str(path_a))
    doc_a.close()

    # Rev B
    doc_b = fitz.open()
    page_b = doc_b.new_page(width=PAGE_W, height=PAGE_H)
    _draw_base(page_b, "B")
    gt = _draw_rev_b_changes(page_b)
    path_b = out / "lift_rev_b.pdf"
    doc_b.save(str(path_b))
    doc_b.close()

    # ground truth: only actual changes
    changes = [g for g in gt if g["change_type"] != "unchanged"]
    gt_path = out / "ground_truth.json"
    payload = {
        "seed": seed,
        "pid_a": "PID-SYN-A",
        "pid_b": "PID-SYN-B",
        "underlying_document_id": "DOC-SYN-LIFT",
        "sha256_a": _sha256(path_a),
        "sha256_b": _sha256(path_b),
        "controlled_changes": changes,
        "unchanged_anchors": [g for g in gt if g["change_type"] == "unchanged"],
        "notes": [
            "Motor vendor does not change (unsupported/no-change for chat).",
            "HH setpoint and duty are intentional modifications.",
        ],
    }
    gt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # update registry entries helper file
    reg_snippet = {
        "PID-SYN-A": {
            "underlying_document_id": "DOC-SYN-LIFT",
            "revision_label": "A",
            "path": "data/samples/synthetic_native/lift_rev_a.pdf",
            "media_type": "application/pdf",
            "display_name": "Synthetic Lift Rev A",
        },
        "PID-SYN-B": {
            "underlying_document_id": "DOC-SYN-LIFT",
            "revision_label": "B",
            "path": "data/samples/synthetic_native/lift_rev_b.pdf",
            "media_type": "application/pdf",
            "display_name": "Synthetic Lift Rev B",
        },
    }
    (out / "registry_snippet.json").write_text(json.dumps(reg_snippet, indent=2), encoding="utf-8")
    print(f"Wrote {path_a}")
    print(f"Wrote {path_b}")
    print(f"Wrote {gt_path}")
    print(f"SHA A={payload['sha256_a'][:12]} B={payload['sha256_b'][:12]}")
    return payload


if __name__ == "__main__":
    main()
