"""Build retrieval indexes from canonical docs + delta."""

from __future__ import annotations

import math

from delta_chat.canonical.models import DocumentRevision
from delta_chat.delta.models import DeltaReport
from delta_chat.retrieval.records import RetrievalRecord, SourceFamily


def build_records(
    doc_a: DocumentRevision,
    doc_b: DocumentRevision,
    delta: DeltaReport,
) -> list[RetrievalRecord]:
    records: list[RetrievalRecord] = []

    def add_doc(doc: DocumentRevision, family: SourceFamily) -> None:
        prefix = {"rev_a": "A", "rev_b": "B", "delta": "D"}[family]
        for page in doc.pages:
            for el in page.elements:
                if not el.normalized_text and el.kind != "geometry_cluster":
                    continue
                text = el.normalized_text or f"[{el.kind}]"
                records.append(
                    RetrievalRecord(
                        source_id=f"{prefix}:p{page.page_number}:{el.element_id}",
                        source_family=family,
                        pid=doc.pid,
                        page=page.page_number,
                        sheet_id=page.sheet_id,
                        grid_region=el.source_ref.grid_region if el.source_ref else None,
                        bbox=list(el.bbox),
                        text=text,
                        identifiers=list(el.identifiers),
                        entity_type=el.kind,
                        confidence=el.extraction_confidence,
                    )
                )
            if page.page_text:
                records.append(
                    RetrievalRecord(
                        source_id=f"{prefix}:p{page.page_number}:page_text",
                        source_family=family,
                        pid=doc.pid,
                        page=page.page_number,
                        sheet_id=page.sheet_id,
                        text=page.page_text[:2000],
                        entity_type="page_text",
                        confidence=1.0,
                    )
                )

    add_doc(doc_a, "rev_a")
    add_doc(doc_b, "rev_b")

    def nearby_identifiers(
        doc: DocumentRevision,
        page_number: int | None,
        bbox: list[float],
        *,
        radius: float = 0.18,
    ) -> list[str]:
        if page_number is None or len(bbox) < 4:
            return []
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        found: set[str] = set()
        for page in doc.pages:
            if page.page_number != page_number:
                continue
            for el in page.elements:
                if len(el.bbox) < 4 or not el.identifiers:
                    continue
                ex = (el.bbox[0] + el.bbox[2]) / 2
                ey = (el.bbox[1] + el.bbox[3]) / 2
                if math.hypot(cx - ex, cy - ey) <= radius:
                    found.update(el.identifiers)
        return sorted(found)

    for c in delta.changes:
        region = c.region or {}
        bbox_b = list(region.get("bbox") or [])
        bbox_a = list(region.get("bbox_a_transformed") or bbox_b)
        identifiers = sorted(
            {
                *nearby_identifiers(doc_a, c.page_a, bbox_a),
                *nearby_identifiers(doc_b, c.page_b, bbox_b),
            }
        )
        text = " ".join(
            x
            for x in [
                c.deterministic_description,
                c.before or "",
                c.after or "",
                c.change_type,
                c.entity_type,
                f"Nearby identifiers: {', '.join(identifiers)}" if identifiers else "",
            ]
            if x
        )
        records.append(
            RetrievalRecord(
                source_id=f"D:{c.delta_item_id}",
                source_family="delta",
                delta_id=delta.delta_id,
                pid=delta.pid_b,
                page=c.page_b or c.page_a,
                grid_region=region.get("grid_region"),
                bbox=bbox_b or bbox_a,
                text=text,
                identifiers=identifiers,
                entity_type=c.entity_type,
                confidence=c.confidence,
                meta={"change_type": c.change_type, "band": c.confidence_band},
            )
        )
    # report summary record
    records.append(
        RetrievalRecord(
            source_id=f"D:{delta.delta_id}:summary",
            source_family="delta",
            delta_id=delta.delta_id,
            text=(
                f"Delta summary between {delta.pid_a} and {delta.pid_b}: "
                f"{delta.summary}. Warnings: {delta.warnings}"
            ),
            entity_type="summary",
            confidence=1.0,
        )
    )
    source_ids = [record.source_id for record in records]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError(
            "Retrieval record source IDs must be unique across both revisions and delta"
        )
    return records
