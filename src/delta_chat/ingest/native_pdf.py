"""Native PDF adapter using positioned text + vector clustering."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import fitz

from delta_chat.canonical.coordinates import normalize_bbox
from delta_chat.canonical.grouping import estimate_grid, make_element, normalize_text
from delta_chat.canonical.models import CanonicalPage, DocumentRevision
from delta_chat.errors import CorruptDocumentError
from delta_chat.pid.models import ResolvedDocument

ADAPTER_NAME = "native_pdf"
ADAPTER_VERSION = "1.1.0"

# Split line spans when horizontal gap exceeds this fraction of page width.
GAP_SPLIT_RATIO = 0.035


def _cluster_drawings(drawings: list[dict], page_w: float, page_h: float) -> list[list[float]]:
    if not drawings:
        return []
    boxes = []
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        boxes.append([float(r.x0), float(r.y0), float(r.x1), float(r.y1)])
    if not boxes:
        return []
    cell = max(page_w, page_h) / 24.0
    buckets: dict[tuple[int, int], list[list[float]]] = defaultdict(list)
    for b in boxes:
        cx = (b[0] + b[2]) / 2
        cy = (b[1] + b[3]) / 2
        key = (int(cx // cell), int(cy // cell))
        buckets[key].append(b)
    clusters = []
    for group in buckets.values():
        if len(group) < 3:
            continue
        x0 = min(g[0] for g in group)
        y0 = min(g[1] for g in group)
        x1 = max(g[2] for g in group)
        y1 = max(g[3] for g in group)
        area = (x1 - x0) * (y1 - y0)
        if area > 0.7 * page_w * page_h or area < 20:
            continue
        clusters.append([x0, y0, x1, y1])
    return clusters[:80]


def _span_items_from_dict(page: fitz.Page) -> list[dict]:
    """Extract localized text runs using PyMuPDF block/line structure."""
    data = page.get_text("dict") or {}
    items: list[dict] = []
    for bi, block in enumerate(data.get("blocks") or []):
        if block.get("type", 0) != 0:
            continue
        for li, line in enumerate(block.get("lines") or []):
            spans = line.get("spans") or []
            if not spans:
                continue
            # Group spans on a line; split on large horizontal gaps or font/size changes
            current: list[dict] = []

            def flush() -> None:
                nonlocal current
                if not current:
                    return
                text = normalize_text(" ".join(s["text"] for s in current if s["text"].strip()))
                if not text:
                    current = []
                    return
                x0 = min(s["bbox"][0] for s in current)
                y0 = min(s["bbox"][1] for s in current)
                x1 = max(s["bbox"][2] for s in current)
                y1 = max(s["bbox"][3] for s in current)
                rot = float(current[0].get("rotation", 0.0))
                items.append(
                    {
                        "text": text,
                        "bbox": [x0, y0, x1, y1],
                        "block_no": bi,
                        "line_no": li,
                        "rotation": rot,
                        "font": current[0].get("font"),
                        "size": current[0].get("size"),
                    }
                )
                current = []

            for si, span in enumerate(spans):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                bbox = span.get("bbox") or [0, 0, 0, 0]
                entry = {
                    "text": text,
                    "bbox": list(map(float, bbox)),
                    "font": span.get("font"),
                    "size": float(span.get("size") or 0),
                    "rotation": float(line.get("dir", [1, 0])[0] != 1) * 90.0,  # rough
                }
                if not current:
                    current.append(entry)
                    continue
                prev = current[-1]
                gap = entry["bbox"][0] - prev["bbox"][2]
                page_w = float(page.rect.width) or 1.0
                font_changed = (entry.get("font") != prev.get("font")) or (
                    abs(float(entry.get("size") or 0) - float(prev.get("size") or 0)) > 0.8
                )
                if gap > page_w * GAP_SPLIT_RATIO or font_changed:
                    flush()
                current.append(entry)
            flush()
    return items


class NativePdfAdapter:
    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def supports(self, path: Path, signals: dict) -> bool:
        return signals.get("adapter") == self.name

    def ingest(
        self,
        resolved: ResolvedDocument,
        *,
        out_dir: Path,
        config: dict,
    ) -> DocumentRevision:
        path = resolved.path
        dpi = int(config.get("render_dpi", 150))
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            doc = fitz.open(path)
        except Exception as exc:  # noqa: BLE001
            raise CorruptDocumentError(str(exc), details={"pid": resolved.pid}) from exc

        pages: list[CanonicalPage] = []
        warnings: list[str] = []
        try:
            for page_index in range(doc.page_count):
                page = doc[page_index]
                pw, ph = float(page.rect.width), float(page.rect.height)
                page_no = page_index + 1

                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                render_path = out_dir / f"{resolved.pid}_p{page_no}.png"
                pix.save(str(render_path))

                span_items = _span_items_from_dict(page)
                elements = []
                page_text_parts = []
                for item in span_items:
                    nb = list(
                        normalize_bbox(
                            item["bbox"],
                            page_width=pw,
                            page_height=ph,
                            origin="top-left",
                        )
                    )
                    grid = estimate_grid(
                        nb,
                        convention="row_letter_col_number",
                        approximate=True,
                    )
                    el = make_element(
                        pid=resolved.pid,
                        page_number=page_no,
                        raw_text=item["text"],
                        bbox=nb,
                        confidence=1.0,
                        attributes={
                            "block_no": item["block_no"],
                            "line_no": item["line_no"],
                            "font": item.get("font"),
                            "size": item.get("size"),
                            "grid_approximate": True,
                        },
                        sheet_id=f"S{page_no}",
                        grid_region=grid,
                    )
                    el.rotation_degrees = float(item.get("rotation") or 0.0)
                    elements.append(el)
                    page_text_parts.append(item["text"])

                drawings = page.get_drawings() or []
                for box in _cluster_drawings(drawings, pw, ph):
                    nb = list(normalize_bbox(box, page_width=pw, page_height=ph, origin="top-left"))
                    el = make_element(
                        pid=resolved.pid,
                        page_number=page_no,
                        raw_text="",
                        bbox=nb,
                        kind="geometry_cluster",
                        confidence=0.7,
                        attributes={"primitive_source": "vector_cluster"},
                        sheet_id=f"S{page_no}",
                        grid_region=estimate_grid(nb, approximate=True),
                    )
                    elements.append(el)

                pages.append(
                    CanonicalPage(
                        page_number=page_no,
                        sheet_id=f"S{page_no}",
                        width=pw,
                        height=ph,
                        render_path=str(render_path),
                        page_text="\n".join(page_text_parts),
                        elements=elements,
                        extraction_metrics={
                            "span_count": len(span_items),
                            "element_count": len(elements),
                            "drawing_count": len(drawings),
                            "dpi": dpi,
                        },
                    )
                )
        finally:
            doc.close()

        if not pages:
            warnings.append("No pages extracted")

        return DocumentRevision(
            pid=resolved.pid,
            underlying_document_id=resolved.underlying_document_id,
            revision_label=resolved.revision_label,
            source_format="native_pdf",
            source_sha256=resolved.sha256,
            adapter_name=ADAPTER_NAME,
            adapter_version=ADAPTER_VERSION,
            pages=pages,
            extraction_warnings=warnings,
            metadata={"media_type": resolved.media_type},
        )
