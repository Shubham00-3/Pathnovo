"""Visual markup overlay on Rev B."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from delta_chat.delta.models import DeltaReport

COLOR = {
    "added": (0, 0.7, 0.2),
    "removed": (0.9, 0.15, 0.15),
    "modified": (0.95, 0.65, 0.1),
    "moved": (0.95, 0.65, 0.1),
    "moved_modified": (0.95, 0.65, 0.1),
}


def _bbox_to_rect(bbox: list[float], page: fitz.Page) -> fitz.Rect:
    w, h = page.rect.width, page.rect.height
    x0, y0, x1, y1 = bbox
    return fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)


def write_markup_pdf(
    report: DeltaReport,
    *,
    source_pdf: Path,
    out_path: Path,
) -> dict[str, Any]:
    """Draw colored boxes on a copy of Rev B PDF (or blank pages if needed)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(source_pdf) if source_pdf.exists() else fitz.open()
    if doc.page_count == 0:
        doc.new_page(width=842, height=595)

    counts = 0
    for change in report.changes:
        page_no = change.page_b or change.page_a or 1
        idx = max(0, min(doc.page_count - 1, page_no - 1))
        page = doc[idx]
        bbox = (change.region or {}).get("bbox") or (change.region or {}).get("bbox_a_transformed")
        if not bbox or len(bbox) < 4:
            continue
        rect = _bbox_to_rect([float(x) for x in bbox[:4]], page)
        color = COLOR.get(change.change_type, (0.6, 0.6, 0.6))
        if change.confidence_band == "low":
            color = (0.55, 0.55, 0.55)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(color=color, width=1.5, dashes="[3 2]" if change.change_type == "removed" else None)
        shape.commit()
        label = change.delta_item_id
        page.insert_text(
            fitz.Point(rect.x0, max(8, rect.y0 - 2)),
            label,
            fontsize=7,
            color=color,
        )
        counts += 1

    # legend on first page
    page0 = doc[0]
    page0.insert_text(
        fitz.Point(20, 20),
        f"Markup: {report.pid_a} -> {report.pid_b} | green=added red=removed amber=mod/move gray=low",
        fontsize=8,
        color=(0.2, 0.2, 0.2),
    )

    doc.save(str(out_path))
    page_count = doc.page_count
    doc.close()
    return {"path": str(out_path), "annotations": counts, "page_count": page_count}
