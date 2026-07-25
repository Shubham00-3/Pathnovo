"""FastAPI HTTP API for the React frontend. Business logic stays in pipeline."""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)\b[A-Z]:[\\/][^\s,;\"']+")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![\w:])/(?:[^/\s]+/)+[^\s,;\"']*")
_SENSITIVE_DETAIL_KEYS = {
    "file",
    "filename",
    "local_path",
    "path",
    "root",
    "run_dir",
    "source_path",
    "source_uri",
}


def _sanitize_public_text(value: str) -> str:
    """Remove host filesystem locations from client-visible error strings."""
    value = _WINDOWS_ABSOLUTE_PATH.sub("<redacted-path>", value)
    return _POSIX_ABSOLUTE_PATH.sub("<redacted-path>", value)


def _sanitize_public_value(value: Any, *, key: str | None = None) -> Any:
    if key and key.lower() in _SENSITIVE_DETAIL_KEYS:
        return "<redacted-path>"
    if isinstance(value, str):
        return _sanitize_public_text(value)
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_public_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_public_value(item) for item in value]
    return value


def _public_delta_error(exc: DeltaChatError) -> dict[str, Any]:
    payload = exc.to_dict()
    return _sanitize_public_value(payload)


class BodyLimitMiddleware:
    """Enforce the request limit for both Content-Length and chunked bodies."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self.max_body_bytes
        ):
            response = JSONResponse({"detail": "Request body too large"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0
        messages: list[Message] = []
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                    await response(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            else:
                break

        async def replay_receive() -> Message:
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


app.add_middleware(BodyLimitMiddleware, max_body_bytes=MAX_BODY_BYTES)


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
    capture = bool(cfg.get("llm", {}).get("capture_content", False))
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


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness plus which optional format capabilities are actually usable.

    A bare "ok" hides the failure that matters most here: the container starts
    fine with no OCR engine and no CAD reader, and the first scanned or DXF
    request is the thing that discovers it. Reporting capability up front makes
    a degraded deployment visible without submitting a document.
    """
    from delta_chat.ingest.ocr import available_backends

    ocr = {
        name: {"available": a.available, "version": a.version, "reason": a.reason}
        for name, a in available_backends(load_config()).items()
    }
    try:
        import ezdxf

        cad = {"available": True, "version": str(ezdxf.__version__)}
    except Exception as exc:  # noqa: BLE001
        cad = {"available": False, "reason": str(exc)}

    return {
        "status": "ok",
        "capabilities": {
            "native_pdf": True,
            "scanned_pdf": any(v["available"] for v in ocr.values()),
            "cad_dxf": cad["available"],
            # DWG needs an external converter on top of the DXF reader.
            "cad_dwg": cad["available"] and bool(_dwg_converter_configured()),
        },
        "ocr_backends": ocr,
        "cad": cad,
    }


def _dwg_converter_configured() -> bool:
    import os
    import shutil

    cfg = load_config().get("dwg", {}) or {}
    candidate = cfg.get("converter_path") or os.environ.get("DWG_CONVERTER_PATH")
    if candidate and Path(candidate).exists():
        return True
    return bool(shutil.which("ODAFileConverter") or shutil.which("dwg2dxf"))


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
        raise HTTPException(status_code=400, detail=_public_delta_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error_type": "InternalServerError", "message": "Internal processing error"},
        ) from exc

    rid = result["request_id"]
    return get_run(rid)


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
        raise HTTPException(status_code=400, detail=_public_delta_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={"error_type": "InternalServerError", "message": "Internal processing error"},
        ) from exc


def _baseline_scorecard() -> dict[str, Any] | None:
    """The committed baseline, shaped like a scorecard.

    `eval/baseline.json` stores the summary itself, so it is wrapped to match
    the artifact layout the UI reads.
    """
    path = project_root() / "eval" / "baseline.json"
    if not path.exists():
        return None
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(summary, dict):
        return None
    return {"summary": summary}


@app.get("/api/eval/latest")
def latest_eval() -> dict[str, Any]:
    """Latest eval scorecard, falling back to the committed baseline.

    Run artifacts are written to the container filesystem and are ephemeral, so
    a fresh deployment has none and the tab would read "no evaluation artifacts
    yet" despite the repo carrying a full scorecard. The baseline is committed,
    so it is always available.

    `source` distinguishes the two: a baseline is a record of a past verified
    run, not evidence that the eval executed on this instance, and the UI must
    not present it as though it were.
    """
    root = project_root() / "artifacts" / "eval"
    runs: list[Path] = []
    if root.exists():
        runs = sorted(
            [p for p in root.iterdir() if p.is_dir() and (p / "scorecard.json").exists()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    if runs:
        latest = runs[0]
        out: dict[str, Any] = {
            "available": True,
            "source": "run",
            "run_id": latest.name,
        }
        out["scorecard"] = json.loads((latest / "scorecard.json").read_text(encoding="utf-8"))
        md = latest / "scorecard.md"
        if md.exists():
            out["scorecard_md"] = md.read_text(encoding="utf-8")
        return out

    baseline = _baseline_scorecard()
    if baseline is None:
        return {"available": False, "source": None}
    return {
        "available": True,
        "source": "baseline",
        "run_id": baseline["summary"].get("run_id"),
        "scorecard": baseline,
    }


# Serve built React app if present
_frontend_dist = project_root() / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
