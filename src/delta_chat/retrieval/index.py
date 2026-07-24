"""Build retrieval indexes from canonical docs + delta."""

from __future__ import annotations

from delta_chat.canonical.models import DocumentRevision
from delta_chat.delta.models import DeltaReport
from delta_chat.retrieval.records import RetrievalRecord


def build_records(
    doc_a: DocumentRevision,
    doc_b: DocumentRevision,
    delta: DeltaReport,
) -> list[RetrievalRecord]:
    records: list[RetrievalRecord] = []

    def add_doc(doc: DocumentRevision, family: str) -> None:
        for page in doc.pages:
            for el in page.elements:
                if not el.normalized_text and el.kind != "geometry_cluster":
                    continue
                text = el.normalized_text or f"[{el.kind}]"
                records.append(
                    RetrievalRecord(
                        source_id=f"{family[0].upper()}:p{page.page_number}:{el.element_id}",
                        source_family=family,  # type: ignore[arg-type]
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
                        source_id=f"{family[0].upper()}:p{page.page_number}:page_text",
                        source_family=family,  # type: ignore[arg-type]
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

    for c in delta.changes:
        text = " ".join(
            x
            for x in [
                c.deterministic_description,
                c.before or "",
                c.after or "",
                c.change_type,
                c.entity_type,
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
                grid_region=(c.region or {}).get("grid_region"),
                bbox=list((c.region or {}).get("bbox") or []),
                text=text,
                identifiers=[],
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
    return records
