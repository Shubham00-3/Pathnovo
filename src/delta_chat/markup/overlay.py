"""Visual markup overlay on Rev B."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from delta_chat.canonical.models import DocumentRevision
from delta_chat.delta.models import DeltaReport

# Longest preview edge in pixels. Enough to read change boxes on screen without
# rasterizing a full-size drawing sheet.
MAX_PREVIEW_PX = 2000.0

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


def _base_document(source_pdf: Path, doc_b: DocumentRevision | None) -> tuple[fitz.Document, str]:
    """Open the best available canvas to draw Rev B markup onto.

    Only the PDF adapters hand us a paper-like source. CAD revisions resolve to a
    .dxf, which MuPDF cannot open, so we fall back to the page renders the
    adapter produced. Returns the document and a label naming the basis, which
    goes into the trace so nobody has to guess what they are looking at.
    """
    if source_pdf.exists():
        try:
            doc = fitz.open(source_pdf)
            if doc.page_count:
                return doc, "source_pdf"
            doc.close()
        except Exception:  # noqa: BLE001
            # Not a PDF (CAD, or an unreadable file): fall through to renders.
            pass

    renders = [
        Path(page.render_path)
        for page in (doc_b.pages if doc_b else [])
        if page.render_path and Path(page.render_path).exists()
    ]
    if renders:
        doc = fitz.open()
        for render in renders:
            pix = fitz.Pixmap(str(render))
            page = doc.new_page(width=pix.width, height=pix.height)
            page.insert_image(page.rect, filename=str(render))
        return doc, "page_render"

    doc = fitz.open()
    for page_spec in doc_b.pages if doc_b else []:
        doc.new_page(width=page_spec.width or 842, height=page_spec.height or 595)
    if doc.page_count == 0:
        doc.new_page(width=842, height=595)
    return doc, "blank"


def write_markup_pdf(
    report: DeltaReport,
    *,
    source_pdf: Path,
    out_path: Path,
    doc_b: DocumentRevision | None = None,
) -> dict[str, Any]:
    """Draw colored boxes for each change on a Rev B canvas."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc, basis = _base_document(source_pdf, doc_b)

    # Each new_shape()/commit() pair emits its own content stream, so drawing one
    # box at a time costs ~15ms. That is invisible on a synthetic pair with six
    # changes and ruinous on a mismatched pair with 624 -- measured at 9.7s p95
    # against a 2s budget. Batch every box sharing a style into one shape per
    # page: same output, one commit per style instead of one per change.
    counts = 0
    batches: dict[tuple[int, tuple[float, float, float], bool], list[fitz.Rect]] = {}
    labels: dict[int, list[tuple[fitz.Point, str, tuple[float, float, float]]]] = {}

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
        dashed = change.change_type == "removed"

        batches.setdefault((idx, color, dashed), []).append(rect)
        labels.setdefault(idx, []).append(
            (fitz.Point(rect.x0, max(8, rect.y0 - 2)), change.delta_item_id, color)
        )
        counts += 1

    for (idx, color, dashed), rects in batches.items():
        shape = doc[idx].new_shape()
        for rect in rects:
            shape.draw_rect(rect)
        shape.finish(color=color, width=1.5, dashes="[3 2]" if dashed else None)
        shape.commit()

    # insert_text() has the same per-call overhead as new_shape(). TextWriter
    # accumulates every label and writes one text object per page.
    for idx, entries in labels.items():
        page = doc[idx]
        writer = fitz.TextWriter(page.rect)
        for point, label, _color in entries:
            writer.append(point, label, fontsize=7)
        # One color per page: the box already encodes change type, and 600
        # per-label colors would need one write_text pass each.
        writer.write_text(page, color=(0.15, 0.15, 0.15))

    # legend on first page
    page0 = doc[0]
    page0.insert_text(
        fitz.Point(20, 20),
        f"Markup: {report.pid_a} -> {report.pid_b} | green=added red=removed amber=mod/move gray=low",
        fontsize=8,
        color=(0.2, 0.2, 0.2),
    )

    doc.save(str(out_path))
    preview_dir = out_path.parent / "crops"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_paths: list[str] = []
    for page_index in range(doc.page_count):
        preview_path = preview_dir / f"markup_p{page_index + 1}.png"
        page = doc[page_index]
        # A fixed zoom rasterizes a large sheet into tens of megapixels for an
        # image no reviewer can use at that size. Bound the longest edge instead,
        # and never upscale. (Cheap on the current fixtures -- the markup cost
        # measured in the budget report was the per-change commits above.)
        longest = max(float(page.rect.width), float(page.rect.height)) or 1.0
        zoom = min(1.5, MAX_PREVIEW_PX / longest)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(str(preview_path))
        preview_paths.append(str(preview_path))
    page_count = doc.page_count
    doc.close()
    return {
        "path": str(out_path),
        "annotations": counts,
        "page_count": page_count,
        "preview_count": len(preview_paths),
        "canvas_basis": basis,
    }
