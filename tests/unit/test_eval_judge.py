from __future__ import annotations

from eval.judges import judge_chat


def test_citation_with_real_id_but_unrelated_quote_is_invalid() -> None:
    result = judge_chat(
        {
            "answer": "The HH setpoint changed to 250.",
            "unsupported": False,
            "citations": [
                {
                    "source_id": "D:D-REAL",
                    "quote": "Duty: 12500 Nm3/h",
                }
            ],
        },
        {
            "must_include": ["250"],
            "require_citation": True,
            "acceptable_source_prefixes": ["D:"],
        },
    )
    assert result["fact_ok"] is True
    assert result["citation_validity"] == 0.0
