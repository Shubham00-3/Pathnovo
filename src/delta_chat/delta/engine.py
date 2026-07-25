"""Deterministic coordinate-aware delta engine."""

from __future__ import annotations

import hashlib
import re
from typing import Any, cast

from delta_chat.canonical.coordinates import transform_bbox_affine
from delta_chat.canonical.limits import enforce_revision_limits
from delta_chat.canonical.models import DocumentRevision
from delta_chat.config import config_hash
from delta_chat.delta.classify import classify_match, confidence_for_change
from delta_chat.delta.compatibility import assess_compatibility, enforce_compatibility
from delta_chat.delta.matching import match_elements
from delta_chat.delta.models import DeltaItem, DeltaReport
from delta_chat.delta.page_align import align_pages
from delta_chat.delta.registration import register_pages
from delta_chat.delta.visual_diff import residual_geometry_changes
from delta_chat.errors import RegistrationFailure


def _desc(
    change_type: str,
    entity: str,
    before: str | None,
    after: str | None,
    page: int | None,
    grid: str | None,
) -> str:
    loc = f"sheet {page or 1}"
    if grid:
        loc += f", grid {grid}"
    if change_type == "added":
        return f"Added {entity}: {after or '(geometry)'} at {loc}."
    if change_type == "removed":
        return f"Removed {entity}: {before or '(geometry)'} at {loc}."
    if change_type == "moved":
        return f"Moved {entity}: {after or before or ''} on {loc}."
    if change_type == "moved_modified":
        return f"Moved and modified {entity} from '{before}' to '{after}' on {loc}."
    return f"Modified {entity} from '{before}' to '{after}' on {loc}."


def _item_id(pid_a: str, pid_b: str, change_type: str, payload: str) -> str:
    digest = hashlib.sha1(f"{pid_a}|{pid_b}|{change_type}|{payload}".encode()).hexdigest()[:8]
    return f"D-{digest.upper()}"


def _is_noise_text(text: str) -> bool:
    t = (text or "").strip().upper()
    if not t:
        return True
    if t.startswith("REV:") or t.startswith("SHEET:"):
        return True
    if re.fullmatch(r"[A-H]|\d", t):
        return True
    if len(t) <= 1:
        return True
    return False


def compute_delta(
    doc_a: DocumentRevision,
    doc_b: DocumentRevision,
    config: dict,
    *,
    mismatch_mode: str | None = None,
) -> DeltaReport:
    enforce_revision_limits(doc_a, config)
    enforce_revision_limits(doc_b, config)
    mode = mismatch_mode or config.get("pair_compatibility", {}).get("mode", "warn")
    compat = assess_compatibility(doc_a, doc_b, config)
    compat = enforce_compatibility(compat, mode)

    page_align = align_pages(doc_a, doc_b)
    warnings: list[str] = []
    if compat.get("warning"):
        warnings.append(compat["warning"])
        for r in compat.get("reasons", []):
            warnings.append(f"pair_mismatch: {r}")

    changes: list[DeltaItem] = []
    registration_all: dict[str, Any] = {}
    suppressed = 0
    move_tol = float(config.get("matching", {}).get("move_centroid_tol", 0.018))
    bands = config.get("confidence", {})
    pair_score = float(compat.get("score", 0.5))

    # page-level add/remove
    for p in page_align.get("unmatched_a", []):
        changes.append(
            DeltaItem(
                delta_item_id=_item_id(doc_a.pid, doc_b.pid, "removed", f"page-{p}"),
                change_type="removed",
                entity_type="page",
                page_a=p,
                before=f"page {p}",
                deterministic_description=f"Removed page {p}.",
                confidence=0.9,
                confidence_band="high",
                review_required=False,
            )
        )
    for p in page_align.get("unmatched_b", []):
        changes.append(
            DeltaItem(
                delta_item_id=_item_id(doc_a.pid, doc_b.pid, "added", f"page-{p}"),
                change_type="added",
                entity_type="page",
                page_b=p,
                after=f"page {p}",
                deterministic_description=f"Added page {p}.",
                confidence=0.9,
                confidence_band="high",
                review_required=False,
            )
        )

    by_page_a = {p.page_number: p for p in doc_a.pages}
    by_page_b = {p.page_number: p for p in doc_b.pages}

    for m in page_align.get("matches", []):
        pa = by_page_a[m["page_a"]]
        pb = by_page_b[m["page_b"]]
        reg: dict[str, Any] = {
            "method": "none",
            "confidence": 0.0,
            "norm_matrix": None,
            "pixel_matrix": None,
            "rejected": True,
            "warning": "registration not run",
        }
        if pa.render_path and pb.render_path:
            try:
                reg = register_pages(pa.render_path, pb.render_path, config)
            except RegistrationFailure as exc:
                warnings.append(str(exc.message))
                reg = {
                    "method": "none",
                    "confidence": 0.0,
                    "norm_matrix": None,
                    "pixel_matrix": None,
                    "rejected": True,
                    "warning": str(exc.message),
                }
        registration_all[str(pb.page_number)] = reg
        # Semantic matching can still use identity spatial coords when registration
        # fails, but visual residual must not run on a fabricated transform.
        norm_matrix = reg.get("norm_matrix")
        matrix = (
            cast(list[list[float]], norm_matrix)
            if isinstance(norm_matrix, list)
            else [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        )
        if reg.get("rejected"):
            warnings.append(reg.get("warning") or "registration rejected")

        # Filter out pure geometry clusters from primary semantic matching volume
        sem_a = [e for e in pa.elements if e.kind != "geometry_cluster" or e.normalized_text]
        sem_b = [e for e in pb.elements if e.kind != "geometry_cluster" or e.normalized_text]
        # Prefer text-bearing elements; include empty geometry sparingly
        if not sem_a:
            sem_a = pa.elements
        if not sem_b:
            sem_b = pb.elements

        matched = match_elements(sem_a, sem_b, matrix=matrix, config=config)
        id_to_a = {e.element_id: e for e in sem_a}
        id_to_b = {e.element_id: e for e in sem_b}

        explained_boxes: list[list[float]] = []

        for match in matched["matches"]:
            ea = id_to_a[match["element_a"]]
            eb = id_to_b[match["element_b"]]
            ctype, extra = classify_match(
                ea,
                eb,
                features=match.get("features") or {},
                matrix=matrix,
                move_tol=move_tol,
            )
            if ctype == "unchanged":
                suppressed += 1
                explained_boxes.append(list(eb.bbox))
                continue
            # Suppress pure revision-label / grid-label noise
            if _is_noise_text(ea.normalized_text) and _is_noise_text(eb.normalized_text):
                suppressed += 1
                continue
            if (ea.normalized_text or "").upper().startswith("REV:") and (
                eb.normalized_text or ""
            ).upper().startswith("REV:"):
                suppressed += 1
                continue
            conf, band, factors = confidence_for_change(
                change_type=ctype,
                match_score=match.get("score"),
                features=match.get("features"),
                extraction_conf=min(ea.extraction_confidence, eb.extraction_confidence),
                registration_conf=float(reg.get("confidence", 0.5)),
                pair_score=pair_score,
                bands=bands,
            )
            grid = (eb.source_ref.grid_region if eb.source_ref else None) or (
                ea.source_ref.grid_region if ea.source_ref else None
            )
            payload = (
                f"{ea.element_id}|{eb.element_id}|{ctype}|{ea.normalized_text}|{eb.normalized_text}"
            )
            item = DeltaItem(
                delta_item_id=_item_id(doc_a.pid, doc_b.pid, ctype, payload),
                change_type=ctype,  # type: ignore[arg-type]
                entity_type=eb.kind or ea.kind,
                page_a=pa.page_number,
                page_b=pb.page_number,
                region={
                    "bbox": eb.bbox,
                    "bbox_a_transformed": list(transform_bbox_affine(ea.bbox, matrix))
                    if matrix
                    else ea.bbox,
                    "grid_region": grid,
                },
                before=ea.normalized_text or None,
                after=eb.normalized_text or None,
                before_ref=ea.source_ref.model_dump() if ea.source_ref else None,
                after_ref=eb.source_ref.model_dump() if eb.source_ref else None,
                deterministic_description=_desc(
                    ctype,
                    eb.kind or ea.kind,
                    ea.normalized_text,
                    eb.normalized_text,
                    pb.page_number,
                    grid,
                ),
                confidence=round(conf, 4),
                confidence_band=band,
                confidence_factors=factors,
                match_features={
                    **match.get("features", {}),
                    **extra,
                    "match_score": match.get("score"),
                },
                review_required=band != "high",
            )
            changes.append(item)
            # Record both sides. A `moved` element leaves ink at its old
            # position and puts ink at the new one; recording only the new box
            # leaves the vacated region unexplained, and the residual pass then
            # reports it as a separate spurious removal.
            explained_boxes.append(list(eb.bbox))
            explained_boxes.append(
                list(transform_bbox_affine(ea.bbox, matrix)) if matrix else list(ea.bbox)
            )

        # unmatched
        for eid in matched["unmatched_a"]:
            ea = id_to_a[eid]
            # suppress tiny empty geometry noise
            if ea.kind == "geometry_cluster" and not ea.normalized_text:
                suppressed += 1
                continue
            if _is_noise_text(ea.normalized_text):
                suppressed += 1
                continue
            conf, band, factors = confidence_for_change(
                change_type="removed",
                match_score=0.75,
                features={"identifier": 1.0 if ea.identifiers else 0.0},
                extraction_conf=ea.extraction_confidence,
                registration_conf=float(reg.get("confidence", 0.5)),
                pair_score=pair_score,
                bands=bands,
            )
            grid = ea.source_ref.grid_region if ea.source_ref else None
            changes.append(
                DeltaItem(
                    delta_item_id=_item_id(
                        doc_a.pid, doc_b.pid, "removed", ea.element_id + ea.normalized_text
                    ),
                    change_type="removed",
                    entity_type=ea.kind,
                    page_a=pa.page_number,
                    page_b=pb.page_number,
                    region={
                        "bbox": list(transform_bbox_affine(ea.bbox, matrix)) if matrix else ea.bbox,
                        "grid_region": grid,
                    },
                    before=ea.normalized_text or None,
                    before_ref=ea.source_ref.model_dump() if ea.source_ref else None,
                    deterministic_description=_desc(
                        "removed", ea.kind, ea.normalized_text, None, pa.page_number, grid
                    ),
                    confidence=round(conf, 4),
                    confidence_band=band,
                    confidence_factors=factors,
                    review_required=band != "high",
                )
            )
            # A removal explains the ink that disappeared at that location. This
            # was previously missing entirely, so every removed element was
            # reported twice: once semantically and once as residual geometry.
            explained_boxes.append(
                list(transform_bbox_affine(ea.bbox, matrix)) if matrix else list(ea.bbox)
            )
        for eid in matched["unmatched_b"]:
            eb = id_to_b[eid]
            if eb.kind == "geometry_cluster" and not eb.normalized_text:
                suppressed += 1
                continue
            if _is_noise_text(eb.normalized_text):
                suppressed += 1
                continue
            conf, band, factors = confidence_for_change(
                change_type="added",
                match_score=0.75,
                features={"identifier": 1.0 if eb.identifiers else 0.0},
                extraction_conf=eb.extraction_confidence,
                registration_conf=float(reg.get("confidence", 0.5)),
                pair_score=pair_score,
                bands=bands,
            )
            grid = eb.source_ref.grid_region if eb.source_ref else None
            changes.append(
                DeltaItem(
                    delta_item_id=_item_id(
                        doc_a.pid, doc_b.pid, "added", eb.element_id + eb.normalized_text
                    ),
                    change_type="added",
                    entity_type=eb.kind,
                    page_a=pa.page_number,
                    page_b=pb.page_number,
                    region={"bbox": eb.bbox, "grid_region": grid},
                    after=eb.normalized_text or None,
                    after_ref=eb.source_ref.model_dump() if eb.source_ref else None,
                    deterministic_description=_desc(
                        "added", eb.kind, None, eb.normalized_text, pb.page_number, grid
                    ),
                    confidence=round(conf, 4),
                    confidence_band=band,
                    confidence_factors=factors,
                    review_required=band != "high",
                )
            )
            explained_boxes.append(list(eb.bbox))

        # residual visual geometry only when registration succeeded with a real matrix
        if (
            not reg.get("rejected")
            and reg.get("pixel_matrix")
            and pa.render_path
            and pb.render_path
            and config.get("visual_diff", {}).get("enabled", True)
        ):
            pixel_matrix = cast(list[list[float]], reg["pixel_matrix"])
            comps = residual_geometry_changes(
                pa.render_path,
                pb.render_path,
                pixel_matrix=pixel_matrix,
                config=config,
                pid_b=doc_b.pid,
                page_number=pb.page_number,
                existing_boxes=explained_boxes,
            )
            suppressed += sum(int(comp.get("suppressed_peers") or 0) for comp in comps)
            for i, component in enumerate(comps):
                ctype = str(component.get("change_type") or "added")
                if ctype not in {"added", "removed", "modified"}:
                    ctype = "added"
                conf, band, factors = confidence_for_change(
                    change_type=ctype,
                    match_score=0.55,
                    features={"geometry": 0.8, "spatial": 0.6},
                    extraction_conf=0.6,
                    registration_conf=float(reg.get("confidence") or 0.0),
                    pair_score=pair_score,
                    bands=bands,
                )
                changes.append(
                    DeltaItem(
                        delta_item_id=_item_id(
                            doc_a.pid,
                            doc_b.pid,
                            ctype,
                            f"geo-{pb.page_number}-{i}-{component['bbox']}",
                        ),
                        change_type=ctype,  # type: ignore[arg-type]
                        entity_type="geometry_region",
                        page_a=pa.page_number,
                        page_b=pb.page_number,
                        region={
                            "bbox": component["bbox"],
                            "grid_region": component.get("grid_region"),
                        },
                        before="geometry region" if ctype == "removed" else None,
                        after="geometry region" if ctype != "removed" else None,
                        deterministic_description=_desc(
                            ctype,
                            "geometry_region",
                            "geometry region" if ctype == "removed" else None,
                            "geometry region" if ctype != "removed" else None,
                            pb.page_number,
                            component.get("grid_region"),
                        ),
                        confidence=round(conf, 4),
                        confidence_band=band,
                        confidence_factors=factors,
                        match_features={
                            "area": component.get("area"),
                            "source": "visual_residual",
                            "direction": component.get("direction"),
                        },
                        review_required=True,
                    )
                )

    # stable ordering
    changes.sort(
        key=lambda c: (
            c.page_b or c.page_a or 0,
            (c.region.get("bbox") or [0, 0, 0, 0])[1],
            (c.region.get("bbox") or [0, 0, 0, 0])[0],
            c.entity_type,
            c.delta_item_id,
        )
    )

    by_type: dict[str, int] = {}
    by_band: dict[str, int] = {}
    for change in changes:
        by_type[change.change_type] = by_type.get(change.change_type, 0) + 1
        by_band[change.confidence_band] = by_band.get(change.confidence_band, 0) + 1

    # Hash the change content, not just how many there are. Keying on
    # len(changes) meant two runs that found the same *number* of entirely
    # different changes produced an identical delta_id, so the id could not be
    # used to tell whether a delta actually changed. Item ids are already
    # content-derived and sorted for order independence.
    change_fingerprint = "|".join(sorted(c.delta_item_id for c in changes))
    delta_id = hashlib.sha1(
        f"{doc_a.pid}|{doc_b.pid}|{config_hash(config)}|"
        f"{len(changes)}|{change_fingerprint}".encode()
    ).hexdigest()[:12]

    return DeltaReport(
        delta_id=delta_id,
        pid_a=doc_a.pid,
        pid_b=doc_b.pid,
        revision_a=doc_a.revision_label,
        revision_b=doc_b.revision_label,
        pair_compatibility=compat,
        config_hash=config_hash(config),
        summary={
            "total_changes": len(changes),
            "by_change_type": by_type,
            "by_confidence_band": by_band,
            "suppressed_unchanged_or_noise": suppressed,
            "cross_document": not compat.get("compatible", True),
        },
        changes=changes,
        warnings=warnings,
        metrics={"change_count": len(changes), "suppressed": suppressed},
        registration=registration_all,
        page_alignment=page_align if isinstance(page_align, dict) else {},
    )
