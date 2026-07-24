from delta_chat.canonical.grouping import make_element
from delta_chat.delta.classify import classify_match, confidence_for_change
from delta_chat.delta.matching import score_pair


def test_score_and_classify():
    a = make_element(pid="A", page_number=1, raw_text="HH 245", bbox=[0.1, 0.1, 0.2, 0.15])
    b = make_element(pid="B", page_number=1, raw_text="HH 250", bbox=[0.11, 0.11, 0.21, 0.16])
    s, feats = score_pair(a, b, matrix=None, weights={
        "identifier": 0.3, "text": 0.22, "spatial": 0.18, "type": 0.1, "neighbor": 0.1, "geometry": 0.1
    })
    assert s > 0.3
    ctype, _ = classify_match(a, b, features=feats, matrix=None, move_tol=0.018)
    assert ctype == "modified"
    conf, band, factors = confidence_for_change(
        change_type=ctype,
        match_score=s,
        features=feats,
        extraction_conf=1.0,
        registration_conf=0.9,
        pair_score=0.9,
        bands={"high": 0.78, "medium": 0.55},
    )
    assert 0 <= conf <= 1
    assert band in {"high", "medium", "low"}
    assert "match_score" in factors
