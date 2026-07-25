"""Spatial re-ranking for "what changed near <tag>" questions."""

from __future__ import annotations

from delta_chat.retrieval.hybrid import HybridRetriever, route_query
from delta_chat.retrieval.records import RetrievalRecord

CONFIG = {
    "retrieval": {"top_k": 6, "rrf_k": 60, "proximity_weight": 0.6, "proximity_radius_norm": 0.18}
}


def _records() -> list[RetrievalRecord]:
    return [
        # The anchor element itself.
        RetrievalRecord(
            source_id="B:p1:pit",
            source_family="rev_b",
            pid="PID-B",
            page=1,
            bbox=[0.46, 0.20, 0.53, 0.21],
            text="26-PIT-9080",
            identifiers=["26-PIT-9080"],
        ),
        # Change directly under the anchor; its own text never names the tag.
        RetrievalRecord(
            source_id="D:near",
            source_family="delta",
            delta_id="D-1",
            page=1,
            bbox=[0.475, 0.171, 0.508, 0.184],
            text="Modified table_cell from 'HH 180' to 'HH 195'",
            identifiers=["26-PIT-9080"],
        ),
        # Unrelated change far away that merely lists the tag as a neighbour.
        RetrievalRecord(
            source_id="D:far",
            source_family="delta",
            delta_id="D-2",
            page=1,
            bbox=[0.40, 0.60, 0.47, 0.62],
            text='Removed text: 6"-PG-2002-A1',
            identifiers=["26-PIT-9080"],
        ),
    ]


def test_proximity_intent_is_detected():
    assert route_query("What changed near 26-PIT-9080?")["proximity"]
    assert route_query("did any dimensions change around the pump")["proximity"]
    assert not route_query("What is the duty value in revision B?")["proximity"]


def test_nearby_change_outranks_a_distant_one_sharing_the_tag():
    retriever = HybridRetriever(_records(), CONFIG)

    hits = retriever.search("What changed near 26-PIT-9080?", top_k=6)
    order = [h["source_id"] for h in hits]

    assert order.index("D:near") < order.index("D:far")


def test_a_delta_record_cannot_anchor_itself():
    """Delta records carry neighbour tags; anchoring on them gives every change
    a perfect proximity score and destroys the ranking entirely."""
    retriever = HybridRetriever(_records(), CONFIG)

    hits = {h["source_id"]: h["score"] for h in retriever.search("What changed near 26-PIT-9080?")}

    assert hits["D:near"] > hits["D:far"]


def test_non_proximity_queries_are_unaffected():
    retriever = HybridRetriever(_records(), CONFIG)

    hits = retriever.search("What is the HH setpoint?", top_k=6)

    assert hits  # ranking still works, no spatial term applied
    assert all("score" in h for h in hits)


def test_records_without_a_bbox_do_not_break_ranking():
    records = _records()
    records.append(
        RetrievalRecord(
            source_id="A:p1:page_text",
            source_family="rev_a",
            pid="PID-A",
            page=1,
            bbox=[],
            text="page level text 26-PIT-9080",
            identifiers=[],
        )
    )

    hits = HybridRetriever(records, CONFIG).search("What changed near 26-PIT-9080?", top_k=6)

    assert hits
