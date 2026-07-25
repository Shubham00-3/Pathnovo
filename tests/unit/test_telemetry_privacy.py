from __future__ import annotations

import json

from delta_chat.observability.llm_telemetry import LLMTelemetry


def test_telemetry_content_capture_is_opt_in(tmp_path) -> None:
    path = tmp_path / "llm_calls.jsonl"
    telemetry = LLMTelemetry(path, capture_content=False)
    telemetry.record(
        provider="fake",
        prompt="private drawing content",
        response="private answer",
        total_tokens=4,
    )
    record = json.loads(path.read_text(encoding="utf-8"))

    assert "prompt" not in record
    assert "response" not in record
    assert record["prompt_hash"]
    assert record["response_hash"]
