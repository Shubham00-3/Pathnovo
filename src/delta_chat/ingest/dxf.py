"""CAD adapter: DXF end-to-end, DWG via a configured converter.

A CAD file is the richest input in this pipeline -- exact coordinates, layers,
block references and text entities, with no OCR or rasterization in between. The
work here is mapping that structure onto the same canonical elements the PDF
adapters emit, so the delta engine cannot tell where a document came from.

DWG is a proprietary binary format with no usable open-source reader. Rather
than pretend otherwise, `.dwg` inputs are converted to DXF by an external
converter (ODA File Converter is the usual choice) and then take the identical
path. Without a converter the failure is explicit and names the fix.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import fitz

from delta_chat.canonical.grouping import estimate_grid, make_element, normalize_text
from delta_chat.canonical.models import CanonicalElement, CanonicalPage, DocumentRevision
from delta_chat.errors import CorruptDocumentError, ResourceLimitError, UnsupportedFormatError
from delta_chat.pid.models import ResolvedDocument

ADAPTER_NAME = "dxf"
ADAPTER_VERSION = "1.0.0"

# Entities carrying human-readable content vs. entities that are pure geometry.
TEXT_ENTITIES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
DIMENSION_ENTITIES = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION"}
GEOMETRY_ENTITIES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "CIRCLE",
    "ARC",
    "ELLIPSE",
    "SPLINE",
    "SOLID",
    "HATCH",
}

# Page geometry for the rendered preview (ISO A3 landscape, in points).
PAGE_WIDTH = 1191.0
PAGE_HEIGHT = 842.0


def _convert_dwg_to_dxf(path: Path, config: dict, out_dir: Path) -> Path:
    """Run the configured DWG->DXF converter. Raises if unavailable."""
    import os
    import shutil

    dwg_cfg = config.get("dwg", {}) or {}
    converter = (
        dwg_cfg.get("converter_path")
        or os.environ.get("DWG_CONVERTER_PATH")
        or shutil.which("ODAFileConverter")
        or shutil.which("odafileconverter")
        or shutil.which("dwg2dxf")
    )
    if not converter or not Path(converter).exists():
        raise UnsupportedFormatError(
            "DWG requires an external converter; none is configured",
            details={
                "detected_format": "dwg",
                "missing_dependency": "ODA File Converter (or dwg2dxf)",
                "suggested_configuration": (
                    "Set dwg.converter_path in config/default.yaml or the "
                    "DWG_CONVERTER_PATH env var. DXF inputs need no converter."
                ),
            },
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="dwg_in_", dir=str(out_dir)))
    shutil.copy2(path, staging / path.name)
    timeout = int(dwg_cfg.get("timeout_seconds", 180))

    name = Path(converter).name.lower()
    if "odafileconverter" in name:
        # ODAFileConverter <in-dir> <out-dir> <version> <type> <recurse> <audit>
        cmd = [
            converter,
            str(staging),
            str(out_dir),
            str(dwg_cfg.get("output_version", "ACAD2018")),
            "DXF",
            "0",
            "1",
        ]
    else:
        cmd = [converter, str(path), str(out_dir / f"{path.stem}.dxf")]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedFormatError(
            "DWG to DXF conversion failed",
            details={
                "detected_format": "dwg",
                "converter": str(converter),
                "error": str(exc)[:400],
                "suggested_configuration": "Verify the converter runs headless on this host",
            },
        ) from exc

    produced = sorted(out_dir.glob(f"{path.stem}.dxf")) or sorted(out_dir.glob("*.dxf"))
    if not produced:
        raise UnsupportedFormatError(
            "DWG converter produced no DXF output",
            details={"detected_format": "dwg", "converter": str(converter)},
        )
    return produced[0]


def _entity_points(entity: Any) -> list[tuple[float, float]]:
    """Best-effort planar extent points for one DXF entity."""
    dxftype = entity.dxftype()
    pts: list[tuple[float, float]] = []
    try:
        if dxftype == "LINE":
            pts = [
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            ]
        elif dxftype == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
        elif dxftype == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        elif dxftype in {"CIRCLE", "ARC"}:
            cx, cy = float(entity.dxf.center.x), float(entity.dxf.center.y)
            r = float(entity.dxf.radius)
            pts = [(cx - r, cy - r), (cx + r, cy + r)]
        elif dxftype == "ELLIPSE":
            cx, cy = float(entity.dxf.center.x), float(entity.dxf.center.y)
            major = float(math.hypot(entity.dxf.major_axis.x, entity.dxf.major_axis.y))
            pts = [(cx - major, cy - major), (cx + major, cy + major)]
        elif dxftype in TEXT_ENTITIES:
            if dxftype == "MTEXT":
                ix, iy = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
                height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)
                width = float(getattr(entity.dxf, "width", 0.0) or 0.0)
                text_len = max(1, len(entity.text or ""))
                span = width or (height * 0.62 * text_len)
                pts = [(ix, iy - height), (ix + span, iy)]
            else:
                ix, iy = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
                height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
                text_len = max(1, len(getattr(entity.dxf, "text", "") or ""))
                pts = [(ix, iy), (ix + height * 0.62 * text_len, iy + height)]
        elif dxftype == "INSERT":
            ix, iy = float(entity.dxf.insert.x), float(entity.dxf.insert.y)
            # Block extents need the block definition; approximate with a scaled
            # unit box so the element still has a usable location.
            sx = abs(float(getattr(entity.dxf, "xscale", 1.0) or 1.0))
            sy = abs(float(getattr(entity.dxf, "yscale", 1.0) or 1.0))
            pts = [(ix - 2.0 * sx, iy - 2.0 * sy), (ix + 2.0 * sx, iy + 2.0 * sy)]
        elif dxftype in DIMENSION_ENTITIES:
            p = (
                entity.dxf.text_midpoint
                if entity.dxf.hasattr("text_midpoint")
                else entity.dxf.defpoint
            )
            pts = [(float(p.x) - 5.0, float(p.y) - 2.0), (float(p.x) + 5.0, float(p.y) + 2.0)]
        elif dxftype == "SPLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity.control_points]
        elif dxftype == "SOLID":
            pts = [
                (float(entity.dxf.vtx0.x), float(entity.dxf.vtx0.y)),
                (float(entity.dxf.vtx2.x), float(entity.dxf.vtx2.y)),
            ]
    except Exception:  # noqa: BLE001
        # A malformed entity must not abort ingestion of the whole drawing.
        return []
    return [p for p in pts if math.isfinite(p[0]) and math.isfinite(p[1])]


def _entity_text(entity: Any) -> str:
    dxftype = entity.dxftype()
    try:
        if dxftype == "MTEXT":
            return normalize_text(entity.plain_text())
        if dxftype in TEXT_ENTITIES:
            return normalize_text(str(getattr(entity.dxf, "text", "") or ""))
        if dxftype in DIMENSION_ENTITIES:
            measurement = getattr(entity.dxf, "actual_measurement", None)
            override = normalize_text(str(getattr(entity.dxf, "text", "") or ""))
            if override and override not in {"<>", ""}:
                return override
            if measurement is not None:
                return f"{float(measurement):.2f}"
        if dxftype == "INSERT":
            name = str(getattr(entity.dxf, "name", "") or "")
            attribs = []
            for att in getattr(entity, "attribs", []) or []:
                value = normalize_text(str(getattr(att.dxf, "text", "") or ""))
                if value:
                    attribs.append(value)
            return normalize_text(" ".join([name, *attribs])) if (name or attribs) else ""
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _kind_for(entity: Any) -> str | None:
    """Canonical kind override, or None to let text classification decide."""
    dxftype = entity.dxftype()
    if dxftype in DIMENSION_ENTITIES:
        return "dimension"
    if dxftype == "INSERT":
        return "symbol"
    if dxftype in GEOMETRY_ENTITIES:
        return "geometry_cluster"
    return None


def _render_preview(
    elements: list[CanonicalElement],
    out_path: Path,
    *,
    title: str,
) -> None:
    """Rasterize a schematic preview so markup and the UI have something to show.

    This is a locator diagram, not a CAD-faithful plot: boxes where the entities
    are, text where the text is. It exists so downstream stages that expect a
    page image keep working; it is never used as delta evidence.
    """
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    for el in elements:
        x0, y0, x1, y1 = (float(v) for v in el.bbox[:4])
        rect = fitz.Rect(
            x0 * PAGE_WIDTH,
            y0 * PAGE_HEIGHT,
            max(x1, x0 + 0.001) * PAGE_WIDTH,
            max(y1, y0 + 0.001) * PAGE_HEIGHT,
        )
        if el.kind == "geometry_cluster":
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(color=(0.45, 0.45, 0.5), width=0.4)
            shape.commit()
        elif el.normalized_text:
            page.insert_textbox(
                rect + (-1, -1, 40, 6),
                el.normalized_text[:60],
                fontsize=max(4.0, min(8.0, rect.height)),
                color=(0.05, 0.05, 0.05),
            )
        else:
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(color=(0.2, 0.4, 0.7), width=0.6)
            shape.commit()
    page.insert_text(fitz.Point(18, 16), title, fontsize=8, color=(0.3, 0.3, 0.3))
    doc.save(str(out_path))
    doc.close()


class DxfAdapter:
    """Maps CAD entities onto the canonical model. Handles .dxf and .dwg."""

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def supports(self, path: Path, signals: dict) -> bool:
        return signals.get("adapter") == self.name or signals.get("format_family") in {"dxf", "dwg"}

    def ingest(
        self,
        resolved: ResolvedDocument,
        *,
        out_dir: Path,
        config: dict,
    ) -> DocumentRevision:
        try:
            import ezdxf
        except ImportError as exc:
            raise UnsupportedFormatError(
                "CAD ingestion requires ezdxf",
                details={
                    "missing_dependency": "ezdxf",
                    "suggested_configuration": "pip install 'delta-chat[cad]'",
                },
            ) from exc

        out_dir.mkdir(parents=True, exist_ok=True)
        source_path = resolved.path
        source_format = "dxf"
        converted_from: str | None = None

        if source_path.suffix.lower() == ".dwg" or _is_dwg(source_path):
            source_path = _convert_dwg_to_dxf(source_path, config, out_dir / "dwg_convert")
            source_format = "dwg"
            converted_from = "dwg"

        try:
            doc = ezdxf.readfile(str(source_path))
        except Exception as exc:  # noqa: BLE001
            raise CorruptDocumentError(
                f"Unreadable DXF: {source_path.name}",
                details={"pid": resolved.pid, "error": str(exc)[:400]},
            ) from exc

        msp = doc.modelspace()
        entities = list(msp)
        max_entities = int(config.get("cad", {}).get("max_entities", 200_000))
        if len(entities) > max_entities:
            raise ResourceLimitError(
                "Drawing exceeds the configured entity limit",
                details={
                    "pid": resolved.pid,
                    "entity_count": len(entities),
                    "max_entities": max_entities,
                },
            )

        # Establish drawing extents so coordinates normalize to [0,1]. Header
        # extents are authoritative when present but are often stale, so they are
        # only trusted if they actually bound the geometry we found.
        collected: list[tuple[Any, list[tuple[float, float]], str]] = []
        for entity in entities:
            pts = _entity_points(entity)
            if not pts:
                continue
            collected.append((entity, pts, entity.dxftype()))

        if not collected:
            raise CorruptDocumentError(
                "DXF contains no positionable modelspace entities",
                details={"pid": resolved.pid, "entity_count": len(entities)},
            )

        xs = [p[0] for _, pts, _ in collected for p in pts]
        ys = [p[1] for _, pts, _ in collected for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)

        def to_norm(pts: list[tuple[float, float]]) -> list[float]:
            px = [(p[0] - min_x) / span_x for p in pts]
            # DXF Y grows upward; canonical space is top-left origin.
            py = [1.0 - (p[1] - min_y) / span_y for p in pts]
            return [
                max(0.0, min(1.0, min(px))),
                max(0.0, min(1.0, min(py))),
                max(0.0, min(1.0, max(px))),
                max(0.0, min(1.0, max(py))),
            ]

        max_elements = int(config.get("max_elements_per_page", 3000))
        elements: list[CanonicalElement] = []
        text_parts: list[str] = []
        skipped_geometry = 0
        layer_counts: dict[str, int] = {}

        for entity, pts, dxftype in collected:
            bbox = to_norm(pts)
            text = _entity_text(entity)
            kind = _kind_for(entity)
            layer = str(getattr(entity.dxf, "layer", "") or "")
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

            # Text-bearing entities are the delta signal; bare geometry is
            # numerous and low-value, so it yields to the element budget first.
            if not text and kind == "geometry_cluster" and len(elements) >= max_elements:
                skipped_geometry += 1
                continue
            if len(elements) >= max_elements:
                skipped_geometry += 1
                continue

            element = make_element(
                pid=resolved.pid,
                page_number=1,
                raw_text=text,
                bbox=bbox,
                kind=kind,  # type: ignore[arg-type]
                confidence=1.0,  # exact vector coordinates, no recognition step
                attributes={
                    "source": "cad",
                    "dxf_type": dxftype,
                    "layer": layer,
                    "block_name": str(getattr(entity.dxf, "name", "") or "") or None,
                    "handle": str(getattr(entity.dxf, "handle", "") or "") or None,
                },
                sheet_id="S1",
                grid_region=estimate_grid(bbox),
            )
            elements.append(element)
            if text:
                text_parts.append(text)

        render_path = out_dir / f"{resolved.pid}_p1.png"
        preview_pdf = out_dir / f"{resolved.pid}_p1.pdf"
        _render_preview(elements, preview_pdf, title=f"{resolved.pid} ({resolved.revision_label})")
        with fitz.open(preview_pdf) as pdoc:
            pdoc[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(render_path))

        warnings: list[str] = []
        if skipped_geometry:
            warnings.append(f"{skipped_geometry} entities dropped at the configured element budget")
        if converted_from:
            warnings.append(
                "Ingested via DWG->DXF conversion; entity fidelity depends on the converter"
            )

        page = CanonicalPage(
            page_number=1,
            sheet_id="S1",
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            render_path=str(render_path),
            page_text="\n".join(text_parts),
            elements=elements,
            extraction_metrics={
                "entity_count": len(entities),
                "positionable_entities": len(collected),
                "element_count": len(elements),
                "text_element_count": len(text_parts),
                "skipped_entities": skipped_geometry,
                "layer_count": len(layer_counts),
                "dxf_version": str(doc.dxfversion),
                "extents": [min_x, min_y, max_x, max_y],
            },
        )

        return DocumentRevision(
            pid=resolved.pid,
            underlying_document_id=resolved.underlying_document_id,
            revision_label=resolved.revision_label,
            source_format=source_format,
            source_sha256=resolved.sha256,
            adapter_name=ADAPTER_NAME,
            adapter_version=ADAPTER_VERSION,
            pages=[page],
            extraction_warnings=warnings,
            metadata={
                "cad_engine": "ezdxf",
                "dxf_version": str(doc.dxfversion),
                "converted_from": converted_from,
                "top_layers": sorted(layer_counts, key=lambda k: -layer_counts[k])[:10],
            },
        )


def _is_dwg(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4).startswith(b"AC10")
    except OSError:
        return False
