"""FastAPI HTTP API for the React frontend. Business logic stays in pipeline."""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from delta_chat.config import load_config, project_root
from delta_chat.errors import DeltaChatError
from delta_chat.observability.context import InvalidRequestIdError, validate_request_id
from delta_chat.pid.local_registry import LocalRegistryResolver
from delta_chat.pipeline import chat_on_run, run_pair

app = FastAPI(
    title="Delta Chat API",
    version="0.1.0",
    description="Document delta engine with grounded chat (local demo; no multi-user auth)",
)

# Local demo only — not hardened for public internet exposure
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_BODY_BYTES = 1_000_000


class RunPairRequest(BaseModel):
    pid_a: str = Field(..., min_length=1, max_length=128)
    pid_b: str = Field(..., min_length=1, max_length=128)
    mismatch_mode: str = Field(default="warn", pattern="^(warn|strict|force)$")
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("request_id")
    @classmethod
    def _rid(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            return validate_request_id(v)
        except InvalidRequestIdError as exc:
            raise ValueError(exc.message) from exc


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)


def _artifacts_root() -> Path:
    cfg = load_config()
    root = project_root()
    art = Path(cfg.get("paths", {}).get("artifacts", "artifacts"))
    return art if art.is_absolute() else root / art


def _run_dir(run_id: str) -> Path:
    try:
        rid = validate_request_id(run_id)
    except InvalidRequestIdError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    path = (_artifacts_root() / "runs" / rid).resolve()
    root = (_artifacts_root() / "runs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid run id") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Run not found: {rid}")
    return path


def _public_paths(run_id: str) -> dict[str, str]:
    return {
        "delta_json": f"/api/runs/{run_id}/file/delta.json",
        "report_md": f"/api/runs/{run_id}/file/report.md",
        "report_html": f"/api/runs/{run_id}/file/report.html",
        "markup_pdf": f"/api/runs/{run_id}/file/markup.pdf",
        "trace": f"/api/runs/{run_id}/file/trace.json",
        "metrics": f"/api/runs/{run_id}/file/metrics.json",
        "events": f"/api/runs/{run_id}/file/events.jsonl",
        "llm_calls": f"/api/runs/{run_id}/file/llm_calls.jsonl",
    }


def _load_run_payload(run_id: str) -> dict[str, Any]:
    from delta_chat.canonical.models import DocumentRevision
    from delta_chat.delta.models import DeltaReport
    from delta_chat.observability.llm_telemetry import LLMTelemetry
    from delta_chat.retrieval.records import RetrievalRecord

    run_dir = _run_dir(run_id)
    delta_path = run_dir / "delta.json"
    if not delta_path.exists():
        raise HTTPException(status_code=404, detail="Run has no delta.json")

    delta = DeltaReport.model_validate_json(delta_path.read_text(encoding="utf-8"))
    doc_a = DocumentRevision.model_validate_json(
        (run_dir / "canonical_a.json").read_text(encoding="utf-8")
    )
    doc_b = DocumentRevision.model_validate_json(
        (run_dir / "canonical_b.json").read_text(encoding="utf-8")
    )
    records_raw = json.loads((run_dir / "retrieval_records.json").read_text(encoding="utf-8"))
    records = [RetrievalRecord.model_validate(r) for r in records_raw]
    cfg = load_config()
    capture = bool(cfg.get("llm", {}).get("capture_content", True))
    telemetry = LLMTelemetry(run_dir / "llm_calls.jsonl", capture_content=capture)
    return {
        "request_id": run_id,
        "run_dir": str(run_dir),  # internal only; not returned to clients
        "report": delta,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "records": records,
        "config": cfg,
        "telemetry": telemetry,
        "delta": delta.model_dump(mode="json"),
    }


@app.middleware("http")
async def body_limit_middleware(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/pids")
def list_pids() -> dict[str, Any]:
    cfg = load_config()
    resolver = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))
    items = []
    for pid in resolver.list_pids():
        try:
            doc = resolver.resolve(pid)
            items.append(
                {
                    "pid": doc.pid,
                    "display_name": doc.display_name,
                    "revision_label": doc.revision_label,
                    "underlying_document_id": doc.underlying_document_id,
                    "media_type": doc.media_type,
                    "byte_size": doc.byte_size,
                }
            )
        except Exception:  # noqa: BLE001
            items.append({"pid": pid, "display_name": pid, "error": "unresolvable"})
    return {"pids": items}


@app.post("/api/run-pair")
def api_run_pair(body: RunPairRequest) -> dict[str, Any]:
    try:
        result = run_pair(
            body.pid_a,
            body.pid_b,
            mismatch_mode=body.mismatch_mode,
            request_id=body.request_id,
        )
    except InvalidRequestIdError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except DeltaChatError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc

    rid = result["request_id"]
    return {
        "request_id": rid,
        "pid_a": result["pid_a"],
        "pid_b": result["pid_b"],
        "delta": result["delta"],
        "paths": _public_paths(rid),
        "summary": result["delta"].get("summary"),
        "pair_compatibility": result["delta"].get("pair_compatibility"),
        "warnings": result["delta"].get("warnings"),
    }


@app.get("/api/runs")
def list_runs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    root = _artifacts_root() / "runs"
    if not root.exists():
        return {"runs": []}
    runs = []
    for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_dir():
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", p.name):
            continue
        item: dict[str, Any] = {"request_id": p.name}
        req = p / "request.json"
        if req.exists():
            try:
                data = json.loads(req.read_text(encoding="utf-8"))
                item["pid_a"] = data.get("pid_a")
                item["pid_b"] = data.get("pid_b")
                item["mismatch_mode"] = data.get("mismatch_mode")
            except Exception:  # noqa: BLE001
                pass
        delta = p / "delta.json"
        if delta.exists():
            try:
                d = json.loads(delta.read_text(encoding="utf-8"))
                item["summary"] = d.get("summary")
                item["pair_compatibility"] = d.get("pair_compatibility")
            except Exception:  # noqa: BLE001
                pass
        runs.append(item)
        if len(runs) >= limit:
            break
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run_dir = _run_dir(run_id)
    delta_path = run_dir / "delta.json"
    if not delta_path.exists():
        raise HTTPException(status_code=404, detail="delta.json missing for this run")
    delta = json.loads(delta_path.read_text(encoding="utf-8"))
    renders = []
    render_dir = run_dir / "renders"
    if render_dir.exists():
        for img in sorted(render_dir.glob("*.png")):
            renders.append(f"/api/runs/{run_id}/file/renders/{img.name}")
    markup_previews = []
    crops = run_dir / "crops"
    if crops.exists():
        for img in sorted(crops.glob("markup_*.png")):
            markup_previews.append(f"/api/runs/{run_id}/file/crops/{img.name}")
    return {
        "request_id": run_id,
        "delta": delta,
        "summary": delta.get("summary"),
        "pair_compatibility": delta.get("pair_compatibility"),
        "warnings": delta.get("warnings"),
        "renders": renders,
        "markup_previews": markup_previews,
        "paths": _public_paths(run_id),
    }


@app.get("/api/runs/{run_id}/file/{file_path:path}")
def get_run_file(run_id: str, file_path: str) -> FileResponse:
    run_dir = _run_dir(run_id)
    # block absolute and parent traversal
    if file_path.startswith("/") or ".." in Path(file_path).parts:
        raise HTTPException(status_code=400, detail="Invalid file path")
    target = (run_dir / file_path).resolve()
    try:
        target.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escape") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media, _ = mimetypes.guess_type(str(target))
    return FileResponse(
        target, media_type=media or "application/octet-stream", filename=target.name
    )


@app.post("/api/runs/{run_id}/chat")
def api_chat(run_id: str, body: ChatRequest) -> dict[str, Any]:
    try:
        payload = _load_run_payload(run_id)
        answer = chat_on_run(payload, body.question)
        return {"question": body.question, "answer": answer, "parent_run_id": run_id}
    except HTTPException:
        raise
    except DeltaChatError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc


@app.get("/api/eval/latest")
def latest_eval() -> dict[str, Any]:
    root = project_root() / "artifacts" / "eval"
    if not root.exists():
        return {"available": False}
    runs = sorted(
        [p for p in root.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not runs:
        return {"available": False}
    latest = runs[0]
    scorecard = latest / "scorecard.json"
    md = latest / "scorecard.md"
    out: dict[str, Any] = {"available": True, "run_id": latest.name}
    if scorecard.exists():
        out["scorecard"] = json.loads(scorecard.read_text(encoding="utf-8"))
    if md.exists():
        out["scorecard_md"] = md.read_text(encoding="utf-8")
    return out


# Serve built React app if present
_frontend_dist = project_root() / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
