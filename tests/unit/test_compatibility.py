from delta_chat.canonical.models import CanonicalPage, DocumentRevision
from delta_chat.delta.compatibility import assess_compatibility


def _doc(pid: str, und: str, text: str, equip: str) -> DocumentRevision:
    from delta_chat.canonical.grouping import make_element

    el = make_element(pid=pid, page_number=1, raw_text=f"{equip} {text}", bbox=[0.1, 0.1, 0.3, 0.2])
    page = CanonicalPage(page_number=1, width=100, height=100, page_text=text, elements=[el])
    return DocumentRevision(
        pid=pid,
        underlying_document_id=und,
        revision_label="A",
        source_format="native_pdf",
        source_sha256="x",
        adapter_name="native_pdf",
        pages=[page],
    )


def test_mismatch_low_score():
    a = _doc("A", "DOC1", "lift compressor service", "26-KA-901")
    b = _doc("B", "DOC2", "export compressor service", "26-KA-902")
    r = assess_compatibility(a, b, {"pair_compatibility": {"threshold": 0.65}})
    assert r["compatible"] is False
    assert r["score"] < 0.65
