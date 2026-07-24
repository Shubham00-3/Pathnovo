"""Scanned PDF adapter: render + preprocess + OCR word boxes."""

from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

from delta_chat.canonical.grouping import estimate_grid, make_element, normalize_text
from delta_chat.canonical.models import CanonicalPage, DocumentRevision
from delta_chat.errors import CorruptDocumentError, OcrFailure
from delta_chat.pid.models import ResolvedDocument

ADAPTER_NAME = "scanned_pdf"
ADAPTER_VERSION = "1.0.0"


def _ocr_available() -> tuple[bool, str]:
    try:
        import pytesseract

        # probe binary
        pytesseract.get_tesseract_version()
        return True, "tesseract"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


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


def _ocr_words(img: np.ndarray, lang: str, min_conf: float) -> list[dict]:
    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(img, lang=lang, output_type=Output.DICT)
    words = []
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:  # noqa: BLE001
            conf = -1
        if conf < min_conf:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(
            {
                "text": text,
                "conf": conf / 100.0 if conf > 1 else conf,
                "bbox_px": [x, y, x + w, y + h],
            }
        )
    return words


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
        ocr_cfg = config.get("ocr", {})
        lang = ocr_cfg.get("lang", "eng")
        min_conf = float(ocr_cfg.get("min_confidence", 40))
        out_dir.mkdir(parents=True, exist_ok=True)

        ok, reason = _ocr_available()
        if not ok:
            raise OcrFailure(
                "Tesseract OCR is not available",
                details={
                    "pid": resolved.pid,
                    "missing_dependency": "tesseract",
                    "reason": reason,
                    "suggested_config": "Install Tesseract and ensure it is on PATH",
                },
            )

        try:
            doc = fitz.open(path)
        except Exception as exc:  # noqa: BLE001
            raise CorruptDocumentError(str(exc), details={"pid": resolved.pid}) from exc

        pages: list[CanonicalPage] = []
        warnings: list[str] = []
        try:
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
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
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
                    words = _ocr_words(pre, lang=lang, min_conf=min_conf)
                except Exception as exc:  # noqa: BLE001
                    raise OcrFailure(
                        f"OCR failed on page {page_no}",
                        details={"pid": resolved.pid, "error": str(exc)},
                    ) from exc

                # group by approximate line
                from collections import defaultdict

                lines: dict[int, list] = defaultdict(list)
                for w in words:
                    y0 = w["bbox_px"][1]
                    lines[int(y0 // 18)].append(w)

                elements = []
                page_text_parts = []
                h_px, w_px = pre.shape[:2]
                for key in sorted(lines.keys()):
                    parts = sorted(lines[key], key=lambda t: t["bbox_px"][0])
                    text = normalize_text(" ".join(p["text"] for p in parts))
                    if not text:
                        continue
                    x0 = min(p["bbox_px"][0] for p in parts)
                    y0 = min(p["bbox_px"][1] for p in parts)
                    x1 = max(p["bbox_px"][2] for p in parts)
                    y1 = max(p["bbox_px"][3] for p in parts)
                    # pixel box -> normalized using image dims (top-left)
                    nb = [
                        x0 / w_px,
                        y0 / h_px,
                        x1 / w_px,
                        y1 / h_px,
                    ]
                    conf = float(np.mean([p["conf"] for p in parts]))
                    el = make_element(
                        pid=resolved.pid,
                        page_number=page_no,
                        raw_text=text,
                        bbox=nb,
                        confidence=conf,
                        attributes={
                            "ocr_raw": text,
                            "ocr_confidence": conf,
                            "source": "ocr",
                        },
                        sheet_id=f"S{page_no}",
                        grid_region=estimate_grid(nb),
                    )
                    elements.append(el)
                    page_text_parts.append(text)

                # light contour geometry regions
                edges = cv2.Canny(pre, 50, 150)
                cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                geo = 0
                for c in cnts:
                    x, y, w, h = cv2.boundingRect(c)
                    if w * h < 400 or w * h > 0.4 * w_px * h_px:
                        continue
                    nb = [x / w_px, y / h_px, (x + w) / w_px, (y + h) / h_px]
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
            metadata={"ocr_engine": "tesseract"},
        )
