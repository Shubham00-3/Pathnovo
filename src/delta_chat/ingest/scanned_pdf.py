"""Scanned PDF adapter: render + preprocess + OCR word boxes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from delta_chat.canonical.grouping import estimate_grid, make_element, normalize_text
from delta_chat.canonical.models import CanonicalPage, DocumentRevision
from delta_chat.errors import CorruptDocumentError, OcrFailure
from delta_chat.ingest.ocr import OcrWord, select_backend
from delta_chat.pid.models import ResolvedDocument

ADAPTER_NAME = "scanned_pdf"
ADAPTER_VERSION = "2.0.0"


def _preprocess(gray: np.ndarray) -> np.ndarray:
    # deskew via moments of edges
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if len(coords) > 100:
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        if angle < -45:
            angle = 90 + angle
        if abs(angle) > 0.2 and abs(angle) < 15:
            (h, w) = gray.shape[:2]
            M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            gray = cv2.warpAffine(
                gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
    # contrast normalize
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _group_into_lines(words: list[OcrWord], *, page_width_px: int) -> list[list[OcrWord]]:
    """Group word-level detections into reading lines.

    Grouping must be *revision-stable*: identical content on Rev A and Rev B has
    to produce identical groups, or the delta engine reports phantom add/remove
    pairs. Fixed-height bucketing fails that test (a band boundary can fall
    between two words on one revision and not the other), so we cluster on
    actual vertical overlap and additionally break a line on a large horizontal
    gap -- which keeps separate columns from merging into one element.
    """
    if not words:
        return []

    max_gap = max(24.0, page_width_px * 0.035)
    ordered = sorted(words, key=lambda w: (w.bbox_px[1], w.bbox_px[0]))

    rows: list[list[OcrWord]] = []
    for word in ordered:
        placed = False
        for row in rows:
            ry0 = min(w.bbox_px[1] for w in row)
            ry1 = max(w.bbox_px[3] for w in row)
            wy0, wy1 = word.bbox_px[1], word.bbox_px[3]
            overlap = min(ry1, wy1) - max(ry0, wy0)
            if overlap > 0.5 * min(ry1 - ry0, wy1 - wy0):
                row.append(word)
                placed = True
                break
        if not placed:
            rows.append([word])

    lines: list[list[OcrWord]] = []
    for row in rows:
        row.sort(key=lambda w: w.bbox_px[0])
        current = [row[0]]
        for prev, word in zip(row, row[1:], strict=False):
            if word.bbox_px[0] - prev.bbox_px[2] > max_gap:
                lines.append(current)
                current = [word]
            else:
                current.append(word)
        lines.append(current)
    return lines


class ScannedPdfAdapter:
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
        dpi = int(config.get("scan_render_dpi", config.get("render_dpi", 200)))
        out_dir.mkdir(parents=True, exist_ok=True)

        # Raises OcrFailure naming every candidate backend and why it was unusable.
        backend, availability = select_backend(config)

        try:
            doc = fitz.open(path)
        except Exception as exc:  # noqa: BLE001
            raise CorruptDocumentError(str(exc), details={"pid": resolved.pid}) from exc

        pages: list[CanonicalPage] = []
        warnings: list[str] = []
        try:
            max_pages = int(config.get("max_pages", 20))
            if doc.page_count > max_pages:
                raise CorruptDocumentError(
                    "Document exceeds the configured page limit",
                    details={"page_count": doc.page_count, "max_pages": max_pages},
                )
            # Guard: scanned adapter must not rely on native text layer.
            native_chars = 0
            for i in range(min(doc.page_count, 2)):
                native_chars += len(doc[i].get_text("text") or "")
            if native_chars > 200:
                warnings.append(
                    "Page has native text layer; scanned adapter still uses OCR path only"
                )

            for page_index in range(doc.page_count):
                page = doc[page_index]
                page_no = page_index + 1
                pw, ph = float(page.rect.width), float(page.rect.height)
                zoom = dpi / 72.0
                render_pixels = int(pw * zoom) * int(ph * zoom)
                max_render_pixels = int(config.get("max_render_pixels", 50_000_000))
                if render_pixels > max_render_pixels:
                    raise CorruptDocumentError(
                        f"Page {page_no} render would exceed the pixel limit",
                        details={
                            "pid": resolved.pid,
                            "render_pixels": render_pixels,
                            "max_render_pixels": max_render_pixels,
                        },
                    )
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img: Any = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                if pix.n == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                pre = _preprocess(gray)
                pre_path = out_dir / f"{resolved.pid}_p{page_no}_pre.png"
                cv2.imwrite(str(pre_path), pre)
                render_path = out_dir / f"{resolved.pid}_p{page_no}.png"
                cv2.imwrite(str(render_path), img)

                try:
                    words = backend.recognize(pre, config=config)
                except OcrFailure:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise OcrFailure(
                        f"OCR failed on page {page_no}",
                        details={
                            "pid": resolved.pid,
                            "backend": backend.name,
                            "error": str(exc),
                        },
                    ) from exc

                h_px, w_px = pre.shape[:2]
                # Line-granular engines already emit reading lines; re-grouping
                # them would reintroduce boundary instability between revisions.
                if getattr(backend, "granularity", "word") == "line":
                    lines = [[w] for w in sorted(words, key=lambda w: (w.bbox_px[1], w.bbox_px[0]))]
                else:
                    lines = _group_into_lines(words, page_width_px=w_px)

                elements = []
                page_text_parts = []
                for parts in lines:
                    text = normalize_text(" ".join(p.text for p in parts))
                    if not text:
                        continue
                    x0 = min(p.bbox_px[0] for p in parts)
                    y0 = min(p.bbox_px[1] for p in parts)
                    x1 = max(p.bbox_px[2] for p in parts)
                    y1 = max(p.bbox_px[3] for p in parts)
                    # pixel box -> normalized using image dims (top-left)
                    nb = [
                        x0 / w_px,
                        y0 / h_px,
                        x1 / w_px,
                        y1 / h_px,
                    ]
                    conf = float(np.mean([p.confidence for p in parts]))
                    el = make_element(
                        pid=resolved.pid,
                        page_number=page_no,
                        raw_text=text,
                        bbox=nb,
                        confidence=conf,
                        attributes={
                            "ocr_raw": text,
                            "ocr_confidence": conf,
                            "ocr_backend": backend.name,
                            "source": "ocr",
                        },
                        sheet_id=f"S{page_no}",
                        grid_region=estimate_grid(nb),
                    )
                    elements.append(el)
                    page_text_parts.append(text)

                # Contour clusters on a raster page are unstable: JPEG noise and
                # sub-pixel resampling move them between scans of the same
                # drawing, and the delta engine then reports add/remove pairs for
                # content that never changed. Measured on the scanned eval case
                # they produced 6 of 11 false positives. Genuine scanned geometry
                # change is covered by the visual-residual path in
                # delta/visual_diff.py, which compares registered pixels instead
                # of guessing at element identity. Off by default; the knob stays
                # so the behaviour can be re-measured.
                emit_contours = bool(config.get("ocr", {}).get("emit_contour_regions", False))
                edges = cv2.Canny(pre, 50, 150)
                cnts, _ = (
                    cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    if emit_contours
                    else ([], None)
                )
                geo = 0
                for contour in cnts:
                    x, y, rect_w, rect_h = cv2.boundingRect(contour)
                    if rect_w * rect_h < 400 or rect_w * rect_h > 0.4 * w_px * h_px:
                        continue
                    nb = [
                        x / w_px,
                        y / h_px,
                        (x + rect_w) / w_px,
                        (y + rect_h) / h_px,
                    ]
                    elements.append(
                        make_element(
                            pid=resolved.pid,
                            page_number=page_no,
                            raw_text="",
                            bbox=nb,
                            kind="geometry_cluster",
                            confidence=0.5,
                            attributes={"source": "contour"},
                            sheet_id=f"S{page_no}",
                            grid_region=estimate_grid(nb),
                        )
                    )
                    geo += 1
                    if geo >= 30:
                        break

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
                            "ocr_word_count": len(words),
                            "ocr_backend": backend.name,
                            "ocr_mean_confidence": (
                                round(float(np.mean([w.confidence for w in words])), 4)
                                if words
                                else 0.0
                            ),
                            "element_count": len(elements),
                            "dpi": dpi,
                            "preprocessed_path": str(pre_path),
                            "native_text_chars_ignored": native_chars,
                        },
                    )
                )
        finally:
            doc.close()

        return DocumentRevision(
            pid=resolved.pid,
            underlying_document_id=resolved.underlying_document_id,
            revision_label=resolved.revision_label,
            source_format="scanned_pdf",
            source_sha256=resolved.sha256,
            adapter_name=ADAPTER_NAME,
            adapter_version=ADAPTER_VERSION,
            pages=pages,
            extraction_warnings=warnings,
            metadata={
                "ocr_engine": backend.name,
                "ocr_engine_version": availability.version,
            },
        )
