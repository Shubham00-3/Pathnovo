from __future__ import annotations

from delta_chat.canonical.models import (
    CanonicalElement,
    CanonicalPage,
    DocumentRevision,
    SourceRef,
)
from delta_chat.delta.models import DeltaItem, DeltaReport
from delta_chat.retrieval.index import build_records


def _document(pid: str, revision: str, value: str) -> DocumentRevision:
    elements = [
        CanonicalElement(
            element_id="pit",
            kind="instrument_tag",
            normalized_text="26-PIT-9062",
            identifiers=["26-PIT-9062"],
            bbox=[0.20, 0.20, 0.30, 0.25],
            source_ref=SourceRef(
                pid=pid,
                page_number=1,
                bbox=[0.20, 0.20, 0.30, 0.25],
            ),
        ),
        CanonicalElement(
            element_id="hh",
            kind="table_cell",
            normalized_text=value,
            bbox=[0.21, 0.26, 0.30, 0.29],
            source_ref=SourceRef(
                pid=pid,
                page_number=1,
                bbox=[0.21, 0.26, 0.30, 0.29],
            ),
        ),
    ]
    return DocumentRevision(
        pid=pid,
        underlying_document_id="DOC-1",
        revision_label=revision,
        source_format="native_pdf",
        source_sha256=revision * 64,
        adapter_name="native_pdf",
        pages=[
            CanonicalPage(
                page_number=1,
                width=100,
                height=100,
                page_text=f"26-PIT-9062 {value}",
                elements=elements,
            )
        ],
    )


def test_revision_ids_are_unique_and_delta_has_nearby_identifier() -> None:
    doc_a = _document("PID-A", "A", "HH 245")
    doc_b = _document("PID-B", "B", "HH 250")
    delta = DeltaReport(
        delta_id="DELTA-1",
        pid_a="PID-A",
        pid_b="PID-B",
        pair_compatibility={"compatible": True},
        config_hash="abc",
        changes=[
            DeltaItem(
                delta_item_id="D-1",
                change_type="modified",
                entity_type="table_cell",
                page_a=1,
                page_b=1,
                region={"bbox": [0.21, 0.26, 0.30, 0.29]},
                before="HH 245",
                after="HH 250",
                deterministic_description="Modified HH 245 to HH 250.",
                confidence=0.9,
                confidence_band="high",
            )
        ],
    )

    records = build_records(doc_a, doc_b, delta)
    source_ids = [record.source_id for record in records]
    delta_record = next(record for record in records if record.source_id == "D:D-1")

    assert len(source_ids) == len(set(source_ids))
    assert any(source_id.startswith("A:") for source_id in source_ids)
    assert any(source_id.startswith("B:") for source_id in source_ids)
    assert "26-PIT-9062" in delta_record.identifiers
    assert "26-PIT-9062" in delta_record.text
