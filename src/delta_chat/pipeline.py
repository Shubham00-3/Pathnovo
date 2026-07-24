"""End-to-end pair pipeline used by CLI, UI, and eval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from delta_chat.canonical.serialization import dump_json
from delta_chat.chat.service import ChatService
from delta_chat.config import load_config, project_root
from delta_chat.delta.engine import compute_delta
from delta_chat.delta.report import write_reports
from delta_chat.errors import DeltaChatError
from delta_chat.ingest import ingest_document
from delta_chat.markup.overlay import write_markup_pdf
from delta_chat.observability.context import RunContext
from delta_chat.observability.llm_telemetry import LLMTelemetry
from delta_chat.observability.logging import EventLogger
from delta_chat.observability.metrics import Metrics
from delta_chat.observability.tracing import Tracer
from delta_chat.pid.local_registry import LocalRegistryResolver
from delta_chat.retrieval.hybrid import HybridRetriever
from delta_chat.retrieval.index import build_records


def run_pair(
    pid_a: str,
    pid_b: str,
    *,
    config: dict | None = None,
    request_id: str | None = None,
    mismatch_mode: str | None = None,
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    root = project_root()
    artifacts = Path(artifacts_root or cfg.get("paths", {}).get("artifacts", "artifacts"))
    if not artifacts.is_absolute():
        artifacts = root / artifacts

    ctx = RunContext.create(artifacts, request_id=request_id)
    logger = EventLogger(ctx.run_dir / "events.jsonl")
    tracer = Tracer(ctx.run_dir / "trace.json", ctx.request_id)
    metrics = Metrics()
    telemetry = LLMTelemetry(
        ctx.run_dir / "llm_calls.jsonl",
        capture_content=bool(cfg.get("llm", {}).get("capture_content", True)),
    )
    errors_path = ctx.run_dir / "errors.jsonl"

    def log_error(exc: Exception) -> None:
        payload = (
            exc.to_dict()
            if isinstance(exc, DeltaChatError)
            else {"error_type": type(exc).__name__, "message": str(exc)}
        )
        with errors_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
        logger.emit("error", **payload)

    request = {
        "request_id": ctx.request_id,
        "pid_a": pid_a,
        "pid_b": pid_b,
        "mismatch_mode": mismatch_mode,
    }
    dump_json(ctx.run_dir / "request.json", request)
    logger.emit("run.start", **request)

    resolver = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))

    try:
        with tracer.span("pid.resolve.a", pid=pid_a) as sp:
            resolved_a = resolver.resolve(pid_a)
            sp["attributes"]["sha256"] = resolved_a.sha256
        with tracer.span("pid.resolve.b", pid=pid_b) as sp:
            resolved_b = resolver.resolve(pid_b)
            sp["attributes"]["sha256"] = resolved_b.sha256

        renders = ctx.run_dir / "renders"
        renders.mkdir(exist_ok=True)

        with tracer.span("ingest.a", pid=pid_a) as sp:
            doc_a, signals_a = ingest_document(resolved_a, out_dir=renders, config=cfg)
            sp["attributes"]["adapter"] = doc_a.adapter_name
            sp["attributes"]["elements"] = sum(len(p.elements) for p in doc_a.pages)
            metrics.incr("elements_a", sp["attributes"]["elements"])
            logger.emit("format.detect.a", **signals_a)

        with tracer.span("ingest.b", pid=pid_b) as sp:
            doc_b, signals_b = ingest_document(resolved_b, out_dir=renders, config=cfg)
            sp["attributes"]["adapter"] = doc_b.adapter_name
            sp["attributes"]["elements"] = sum(len(p.elements) for p in doc_b.pages)
            metrics.incr("elements_b", sp["attributes"]["elements"])
            logger.emit("format.detect.b", **signals_b)

        dump_json(ctx.run_dir / "canonical_a.json", doc_a.model_dump(mode="json"))
        dump_json(ctx.run_dir / "canonical_b.json", doc_b.model_dump(mode="json"))

        with tracer.span("pair.compatibility") as sp:
            # compute_delta also assesses; capture mode
            mode = mismatch_mode or cfg.get("pair_compatibility", {}).get("mode", "warn")
            sp["attributes"]["mode"] = mode

        with tracer.span("delta.engine") as sp:
            report = compute_delta(doc_a, doc_b, cfg, mismatch_mode=mismatch_mode)
            sp["attributes"]["changes"] = len(report.changes)
            metrics.set("delta_changes", len(report.changes))
            metrics.set("pair_compatibility", report.pair_compatibility)

        paths = write_reports(report, ctx.run_dir)
        logger.emit("report.written", **paths)

        with tracer.span("markup.overlay") as sp:
            markup_info = write_markup_pdf(
                report,
                source_pdf=resolved_b.path,
                out_path=ctx.run_dir / "markup.pdf",
            )
            sp["attributes"].update(markup_info)

        with tracer.span("retrieval.index") as sp:
            records = build_records(doc_a, doc_b, report)
            sp["attributes"]["records"] = len(records)
            dump_json(ctx.run_dir / "retrieval_records.json", [r.model_dump() for r in records])

        for span in tracer.spans:
            metrics.set_stage(span["name"], span.get("duration_ms", 0))

        metrics.set(
            "llm",
            {
                "calls": telemetry.calls,
                "total_tokens": telemetry.total_tokens,
                "total_cost": telemetry.total_cost,
            },
        )
        metrics.write(ctx.run_dir / "metrics.json")
        tracer.write()
        logger.emit("run.complete", request_id=ctx.request_id, changes=len(report.changes))

        return {
            "request_id": ctx.request_id,
            "run_dir": str(ctx.run_dir),
            "pid_a": pid_a,
            "pid_b": pid_b,
            "delta": report.model_dump(mode="json"),
            "canonical_a": str(ctx.run_dir / "canonical_a.json"),
            "canonical_b": str(ctx.run_dir / "canonical_b.json"),
            "paths": {
                **paths,
                "markup_pdf": str(ctx.run_dir / "markup.pdf"),
                "trace": str(ctx.run_dir / "trace.json"),
                "metrics": str(ctx.run_dir / "metrics.json"),
                "events": str(ctx.run_dir / "events.jsonl"),
            },
            "doc_a": doc_a,
            "doc_b": doc_b,
            "report": report,
            "records": records,
            "config": cfg,
            "telemetry": telemetry,
        }
    except Exception as exc:  # noqa: BLE001
        log_error(exc)
        metrics.incr_error(type(exc).__name__)
        metrics.write(ctx.run_dir / "metrics.json")
        tracer.write()
        logger.emit("run.failed", error=str(exc))
        raise


def chat_on_run(
    run_payload: dict[str, Any],
    question: str,
    *,
    chat_request_id: str | None = None,
) -> dict[str, Any]:
    """Answer a question against an existing pair run, appending chat spans/metrics."""
    from delta_chat.observability.context import new_request_id, validate_request_id
    from delta_chat.observability.logging import EventLogger
    from delta_chat.observability.metrics import Metrics
    from delta_chat.observability.tracing import Tracer

    report = run_payload["report"]
    records = run_payload["records"]
    cfg = run_payload["config"]
    run_dir = Path(run_payload["run_dir"])
    parent_run_id = run_payload.get("request_id") or run_dir.name

    capture = bool(cfg.get("llm", {}).get("capture_content", True))
    telemetry = run_payload.get("telemetry")
    if telemetry is None:
        from delta_chat.observability.llm_telemetry import LLMTelemetry

        telemetry = LLMTelemetry(run_dir / "llm_calls.jsonl", capture_content=capture)
        run_payload["telemetry"] = telemetry
    else:
        # honor config if telemetry was created elsewhere with hardcoded capture
        telemetry.capture_content = capture

    chat_rid = validate_request_id(chat_request_id) if chat_request_id else new_request_id()
    tracer = Tracer(
        run_dir / "trace.json",
        request_id=chat_rid,
        correlation_id=parent_run_id,
        parent_run_id=parent_run_id,
    )
    logger = EventLogger(run_dir / "events.jsonl")
    metrics = Metrics.load(run_dir / "metrics.json")
    logger.emit(
        "chat.start",
        chat_request_id=chat_rid,
        parent_run_id=parent_run_id,
        question=question[:500],
    )

    retriever = HybridRetriever(records, cfg)
    service = ChatService(
        retriever,
        report,
        cfg,
        telemetry=telemetry,
        tracer=tracer,
    )
    try:
        with tracer.span("chat.request", question_chars=len(question)):
            answer = service.ask(question)
    except Exception as exc:  # noqa: BLE001
        metrics.incr_error(type(exc).__name__)
        metrics.write(run_dir / "metrics.json")
        tracer.write()
        logger.emit("chat.error", error=str(exc), chat_request_id=chat_rid)
        raise

    chat_path = run_dir / "chat.jsonl"
    with chat_path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "chat_request_id": chat_rid,
                    "parent_run_id": parent_run_id,
                    "question": question,
                    "answer": answer.model_dump(mode="json"),
                },
                default=str,
            )
            + "\n"
        )

    # Cumulative LLM metrics from telemetry object (already restored from jsonl)
    metrics.merge_llm(
        calls=0,  # absolute set below from telemetry totals
        total_tokens=0,
        total_cost=None,
        cost_status="unavailable",
        cost_reason="no_provider_pricing_table",
    )
    metrics.data["llm"] = {
        "calls": telemetry.calls,
        "total_tokens": telemetry.total_tokens,
        "total_cost": telemetry.total_cost,
        "cost_status": "unavailable" if telemetry.total_cost is None else "estimated",
        "cost_reason": (
            "no_provider_pricing_table" if telemetry.total_cost is None else "provider_or_heuristic"
        ),
    }
    metrics.incr("chat_requests")
    if answer.unsupported:
        metrics.incr("chat_refusals")
    for sp in tracer.spans:
        if sp.get("request_id") == chat_rid:
            metrics.set_stage(f"chat.{sp['name']}", sp.get("duration_ms", 0))
    metrics.write(run_dir / "metrics.json")
    tracer.write()
    logger.emit(
        "chat.complete",
        chat_request_id=chat_rid,
        unsupported=answer.unsupported,
        citations=len(answer.citations),
        provider=answer.provider,
    )
    return answer.model_dump(mode="json")
