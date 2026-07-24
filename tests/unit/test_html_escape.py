from delta_chat.delta.models import DeltaItem, DeltaReport
from delta_chat.delta.report import render_html


def test_html_escapes_script_payloads():
    report = DeltaReport(
        delta_id="x",
        pid_a="<script>alert(1)</script>",
        pid_b="B",
        pair_compatibility={"compatible": True, "score": 1, "warning": "<img onerror=1>"},
        config_hash="abc",
        summary={"total_changes": 1},
        changes=[
            DeltaItem(
                delta_item_id="D-1",
                change_type="added",
                entity_type="note",
                deterministic_description="<b>bad</b>",
                before=None,
                after="<script>x</script>",
                confidence=0.9,
                confidence_band="high",
            )
        ],
        warnings=["<script>warn</script>"],
    )
    html = render_html(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
