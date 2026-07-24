# Document Delta & Grounded Chat

Format-agnostic pipeline that resolves two **PIDs**, normalizes them into a **canonical representation**, computes a **deterministic, coordinate-aware delta**, writes JSON/Markdown/HTML reports (plus markup), and answers questions with **validated citations**.

**Primary UI:** React (Vite) served by **FastAPI** on port **8000**.

## What is implemented

| Area | Status |
|------|--------|
| Native PDF adapter (block/line spans) | **Implemented** |
| Scanned PDF + OCR (Tesseract) | **Implemented** (requires Tesseract; local Windows may skip eval until installed) |
| DWG adapter | **Seam only** — detect + actionable `UnsupportedFormatError` |
| Deterministic delta engine | **Implemented** |
| Pair mismatch (lift vs export) | **Implemented** |
| Reports + markup PDF | **Implemented** |
| Hybrid retrieval + grounded chat | **Implemented** (citation retry; no silent ID substitution) |
| Chat/LLM spans in trace + cumulative metrics | **Implemented** |
| Eval harness with gates | **Implemented** (`fail_on_gate: true`) |
| React + FastAPI | **Implemented** |
| Docker multi-stage (React + Tesseract + API) | **Implemented** |

### LLM usage (honest)

- Default `LLM_PROVIDER=extractive` is a **deterministic no-key baseline**, not proof that a cloud LLM path was evaluated end-to-end.
- Optional: `pip install '.[llm]'` and set `LLM_PROVIDER` / `LLM_MODEL` / API keys for LiteLLM.
- Token cost is reported as **unavailable** unless the provider returns a real cost field.

### Lift vs Export drawings

Supplied AutoCAD P&IDs (`26-KA-901` lift vs `26-KA-902` export) are **different documents**. They are used only for **pair-mismatch detection**, not as a revision-quality benchmark. They are **not** committed; keep them under gitignored `data/private_inputs/`.

## Quick start (local)

```powershell
cd D:\Pathnovo
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
$env:PYTHONPATH = "src;."

# samples
python -c "from scripts.make_synthetic_pid_pair import main; main()"
python -c "from scripts.make_scanned_pair import main; main()"
python -c "from scripts.build_eval_dataset import main; main()"

# quality
python -m pytest -q
python -m ruff check src tests scripts eval
python -m eval.run

# React production build + API (serves frontend/dist)
cd frontend; npm install; npm run build; cd ..
python -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** (static React) or dev UI at **http://127.0.0.1:5173** with Vite proxy:

```powershell
# terminal 1
python -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000
# terminal 2
cd frontend; npx vite --host 127.0.0.1 --port 5173
```

## Docker (one-command React demo)

```bash
docker compose up --build
# http://localhost:8000
```

- Multi-stage build: Node builds React; Python image installs Tesseract + API.
- Publishes **8000:8000**, healthcheck on `/api/health`.
- **Does not** copy private P&IDs into the image.
- Local-only demo boundary (no multi-user auth).

## Verification

```bash
make verify   # format, ruff, mypy, pytest, frontend build, eval
```

Or manually: `pytest`, `ruff check`, `python -m eval.run`, `cd frontend && npm run build`.

## Architecture

```text
PID A/B → resolver → format detector → native|scanned|dwg adapter
       → canonical JSON → pair compatibility → registration
       → semantic match + residual visual geometry → delta.json
       → reports + markup → retrieval index → grounded chat
       → React UI via FastAPI /api/*
```

## Known limits

| Symptom | Why | Mitigation |
|---------|-----|------------|
| Scanned eval skipped without Tesseract | OCR binary missing | Install Tesseract; Docker image includes it |
| Dense multi-sheet CAD | Registration / layout redesign | Reject low-confidence registration; suppress visual residual |
| OCR digit confusion | Raster noise | Keep raw OCR + confidence; refuse weak evidence |
| DWG not E2E | No bundled converter | Visible error with config hint |
| Extractive chat is not an LLM judge | Deterministic baseline | Optional LiteLLM path when keys present |

## Security / privacy

- `request_id` must match `[A-Za-z0-9_-]{1,64}`; path traversal rejected.
- API responses expose URL paths only (no absolute host filesystem paths).
- HTML reports escape extracted text.
- Private inputs, `.env`, artifacts, and planning files are gitignored / dockerignored.

## Sample provenance

See [`data/samples/README.md`](data/samples/README.md). Synthetic Rev A/B are drawn **independently** (no white-out of hidden PDF text).

## CLI

```bash
delta-chat run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B
delta-chat chat --run-id <id> -q "What changed near 26-PIT-9062?"
python -m eval.run
```
