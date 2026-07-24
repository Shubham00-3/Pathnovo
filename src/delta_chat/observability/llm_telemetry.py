"""LLM call telemetry to llm_calls.jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class LLMTelemetry:
    def __init__(self, path: Path, capture_content: bool = True) -> None:
        self.path = path
        self.capture_content = capture_content
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        self.total_tokens = 0
        self.total_cost = 0.0
        self.calls = 0

    def record(self, **fields: Any) -> None:
        self.calls += 1
        tokens = int(fields.get("total_tokens") or 0)
        cost = float(fields.get("estimated_cost") or 0.0)
        self.total_tokens += tokens
        self.total_cost += cost
        if not self.capture_content:
            fields.pop("prompt", None)
            fields.pop("response", None)
            fields["prompt_hash"] = fields.get("prompt_hash")
        record = {"ts": time.time(), **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
