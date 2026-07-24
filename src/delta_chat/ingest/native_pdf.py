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
ADAPTER_VERSION = "1.0.0"


def _cluster_drawings(drawings: list[dict], page_w: float, page_h: float) -> list[list[float]]:
    """Spatially cluster vector primitives into coarse geometry regions."""
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
    # Quantize into a coarse grid to avoid thousands of primitives.
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
        # Skip near-full-page noise
        area = (x1 - x0) * (y1 - y0)
        if area > 0.7 * page_w * page_h:
            continue
        if area < 20:
            continue
        clusters.append([x0, y0, x1, y1])
    return clusters[:80]


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

                # Render
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                render_path = out_dir / f"{resolved.pid}_p{page_no}.png"
                pix.save(str(render_path))

                # Positioned words
                words = page.get_text("words") or []
                # group words into lines by y proximity
                lines: dict[int, list] = defaultdict(list)
                for w in words:
                    x0, y0, x1, y1, text, *_ = w
                    if not str(text).strip():
                        continue
                    # y bucket ~ 3 PDF points
                    yb = int(round(float(y0) / 3.0))
                    lines[yb].append((float(x0), float(y0), float(x1), float(y1), str(text)))

                elements = []
                page_text_parts = []
                for yb in sorted(lines.keys()):
                    parts = sorted(lines[yb], key=lambda t: t[0])
                    text = normalize_text(" ".join(p[4] for p in parts))
                    if not text:
                        continue
                    x0 = min(p[0] for p in parts)
                    y0 = min(p[1] for p in parts)
                    x1 = max(p[2] for p in parts)
                    y1 = max(p[3] for p in parts)
                    nb = list(
                        normalize_bbox(
                            [x0, y0, x1, y1],
                            page_width=pw,
                            page_height=ph,
                            origin="top-left",
                        )
                    )
                    grid = estimate_grid(nb)
                    el = make_element(
                        pid=resolved.pid,
                        page_number=page_no,
                        raw_text=text,
                        bbox=nb,
                        confidence=1.0,
                        sheet_id=f"S{page_no}",
                        grid_region=grid,
                    )
                    elements.append(el)
                    page_text_parts.append(text)

                # Geometry clusters
                drawings = page.get_drawings() or []
                for box in _cluster_drawings(drawings, pw, ph):
                    nb = list(
                        normalize_bbox(box, page_width=pw, page_height=ph, origin="top-left")
                    )
                    el = make_element(
                        pid=resolved.pid,
                        page_number=page_no,
                        raw_text="",
                        bbox=nb,
                        kind="geometry_cluster",
                        confidence=0.7,
                        attributes={"primitive_source": "vector_cluster"},
                        sheet_id=f"S{page_no}",
                        grid_region=estimate_grid(nb),
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
                            "word_count": len(words),
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
