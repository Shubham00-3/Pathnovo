"""Swappable LLM clients. Extractive is default (no API key)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

from delta_chat.errors import LLMTimeoutError
from delta_chat.observability.llm_telemetry import LLMTelemetry


class LLMClient(Protocol):
    provider: str

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]: ...


class ExtractiveLLMClient:
    """Deterministic no-key baseline: extractive answer from evidence."""

    provider = "extractive"

    def __init__(self, telemetry: LLMTelemetry | None = None) -> None:
        self.telemetry = telemetry

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        start = time.time()
        # evidence blocks marked with [source_id]
        blocks = re.findall(r"\[(D:[^\]]+|A:[^\]]+|B:[^\]]+)\]\s*(.+?)(?=\n\[|\Z)", prompt, re.S)
        qmatch = re.search(r"Question:\s*(.+)", prompt)
        question = qmatch.group(1).strip() if qmatch else ""
        ql = question.lower()
        stop = {
            "what",
            "which",
            "where",
            "when",
            "near",
            "about",
            "value",
            "table",
            "revision",
            "between",
            "changed",
            "change",
            "from",
            "with",
            "that",
            "this",
            "only",
            "were",
            "was",
            "the",
            "and",
            "did",
        }
        q_tokens = {token for token in re.findall(r"[a-z0-9\-]{2,}", ql) if token not in stop}
        q_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", ql))
        asks_number = any(word in ql for word in ("value", "duty", "setpoint", "how many"))
        asks_change = any(word in ql for word in ("change", "changed", "added", "removed", "moved"))
        asks_rev_b = any(word in ql for word in ("revision b", "rev b", "current", "after", "new"))

        ranked: list[tuple[float, str, str]] = []
        for position, (sid_raw, raw_text) in enumerate(blocks[:12]):
            sid = sid_raw.strip()
            snippet = raw_text.strip().splitlines()[0][:240]
            low = snippet.lower()
            tokens = set(re.findall(r"[a-z0-9\-]{2,}", low))
            score = 3.0 * len(q_tokens & tokens)
            score += 2.0 * sum(1 for token in q_tokens if token in low)
            score += 6.0 * sum(1 for number in q_numbers if number in low)
            if asks_number and re.search(r"\d", snippet):
                score += 2.0
            if asks_change and sid.startswith("D:"):
                score += 1.5
            if asks_rev_b and sid.startswith("B:"):
                score += 2.5
            score -= position * 0.01
            ranked.append((score, sid, snippet))
        ranked.sort(key=lambda item: item[0], reverse=True)
        citations = [sid for _score, sid, _snippet in ranked]
        snippets = [snippet for _score, _sid, snippet in ranked]

        if not snippets:
            result: dict[str, Any] = {
                "answer": (
                    "I could not find enough evidence in the provided PIDs or delta report to answer that."
                ),
                "citations": [],
                "confidence": "low",
                "unsupported": True,
            }
        elif any(w in ql for w in ("summarize", "high-confidence", "high confidence")):
            result = {
                "answer": "High-confidence / retrieved changes: " + " | ".join(snippets[:5]),
                "citations": citations[:5],
                "confidence": "high",
                "unsupported": False,
            }
        elif "how many" in ql and "added" in ql:
            # count from evidence lines mentioning added
            n = sum(1 for s in snippets if "added" in s.lower())
            result = {
                "answer": f"Based on retrieved delta evidence, about {n} added items appear in the top evidence set.",
                "citations": citations[:3] or citations,
                "confidence": "medium",
                "unsupported": False,
            }
        else:
            result = {
                "answer": snippets[0],
                "citations": citations[:1],
                "confidence": "medium",
                "unsupported": False,
            }

        latency = (time.time() - start) * 1000
        if self.telemetry:
            answer_text = str(result["answer"])
            self.telemetry.record(
                provider=self.provider,
                model="extractive",
                temperature=0,
                prompt=prompt[:2000],
                response=json.dumps(result),
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(answer_text.split()),
                total_tokens=len(prompt.split()) + len(answer_text.split()),
                estimated_cost=None,
                cost_status="unavailable",
                cost_reason="extractive_provider_has_no_token_pricing",
                latency_ms=round(latency, 2),
                status="ok",
            )
        return result


class FakeLLMClient:
    provider = "fake"

    def __init__(
        self, response: dict[str, Any] | None = None, telemetry: LLMTelemetry | None = None
    ) -> None:
        self.response = response or {
            "answer": "fake",
            "citations": [],
            "confidence": "low",
            "unsupported": True,
        }
        self.telemetry = telemetry

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        if self.telemetry:
            self.telemetry.record(
                provider="fake",
                model="fake",
                prompt=prompt[:500],
                response=json.dumps(self.response),
                total_tokens=10,
                estimated_cost=0.0,
                latency_ms=1,
                status="ok",
            )
        return dict(self.response)


class LiteLLMClient:
    provider = "litellm"

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 800,
        telemetry: LLMTelemetry | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.telemetry = telemetry
        self.timeout_s = timeout_s
        self.provider = "litellm"

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover
            raise LLMTimeoutError(
                "litellm is not installed; pip install 'delta-chat[llm]' or use LLM_PROVIDER=extractive",
                details={"missing_dependency": "litellm"},
            ) from exc

        start = time.time()
        try:
            resp = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system or "Return strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            if self.telemetry:
                self.telemetry.record(
                    provider="litellm",
                    model=self.model,
                    status="error",
                    error=str(exc),
                    latency_ms=round((time.time() - start) * 1000, 2),
                )
            raise LLMTimeoutError(str(exc), details={"model": self.model}) from exc

        content = resp.choices[0].message.content or "{}"
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total = prompt_tokens + completion_tokens
        # Cost: use provider usage if available; otherwise mark unavailable (no guessed pricing)
        cost = None
        cost_status = "unavailable"
        cost_reason = "no_provider_pricing_table"
        try:
            hidden = getattr(resp, "_hidden_params", None) or {}
            if isinstance(hidden, dict) and hidden.get("response_cost") is not None:
                cost = float(hidden["response_cost"])
                cost_status = "provider"
                cost_reason = "litellm_response_cost"
        except Exception:  # noqa: BLE001
            pass
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Do not invent citations for raw text; let chat service refuse/retry
            data = {
                "answer": content,
                "citations": [],
                "confidence": "low",
                "unsupported": True,
            }
        if self.telemetry:
            self.telemetry.record(
                provider="litellm",
                model=self.model,
                temperature=self.temperature,
                prompt=prompt,
                response=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
                estimated_cost=cost,
                cost_status=cost_status,
                cost_reason=cost_reason,
                latency_ms=round((time.time() - start) * 1000, 2),
                status="ok",
            )
        return data


class FallbackLLMClient:
    """Use the deterministic client when the hosted provider is unavailable.

    The primary failure is still captured by ``LiteLLMClient`` telemetry. This
    keeps the public demo usable during free-tier throttling without pretending
    that the fallback answer came from the hosted model.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self.primary = primary
        self.fallback = fallback
        self.provider = primary.provider

    def answer(self, prompt: str, *, system: str = "") -> dict[str, Any]:
        try:
            result = self.primary.answer(prompt, system=system)
            self.provider = self.primary.provider
            return result
        except Exception:  # noqa: BLE001
            self.provider = f"{self.fallback.provider}_fallback"
            return self.fallback.answer(prompt, system=system)


def build_llm_client(config: dict, telemetry: LLMTelemetry | None = None) -> Any:
    lcfg = config.get("llm", {})
    provider = (os.environ.get("LLM_PROVIDER") or lcfg.get("provider") or "extractive").lower()
    model = os.environ.get("LLM_MODEL") or lcfg.get("model") or ""
    if provider in {"extractive", "none", "deterministic"}:
        return ExtractiveLLMClient(telemetry=telemetry)
    if provider == "fake":
        return FakeLLMClient(telemetry=telemetry)
    if not model:
        return ExtractiveLLMClient(telemetry=telemetry)
    primary = LiteLLMClient(
        model=model,
        temperature=float(lcfg.get("temperature", 0.0)),
        max_tokens=int(lcfg.get("max_tokens", 800)),
        telemetry=telemetry,
    )
    return FallbackLLMClient(primary, ExtractiveLLMClient(telemetry=telemetry))
