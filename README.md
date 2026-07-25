# Document Delta & Grounded Chat

Format-agnostic pipeline that resolves two **PIDs**, normalizes them into a **canonical representation**, computes a **deterministic, coordinate-aware delta**, writes JSON/Markdown/HTML reports (plus a markup overlay), and answers questions with **validated citations**.

**Primary UI:** React (Vite) served by **FastAPI** on port **8000**.

## What is implemented

| Area | Status |
|------|--------|
| Native PDF adapter (block/line spans) | **End-to-end** |
| Scanned PDF + OCR | **End-to-end** — pluggable backend, RapidOCR (ONNX, pip-only) by default, Tesseract optional |
| CAD adapter (DXF) | **End-to-end** via `ezdxf` — entities, layers, blocks, dimensions |
| DWG | **Seam is real** — same adapter, converts to DXF via an external converter; explicit error without one |
| Deterministic delta engine | Registration → Hungarian matching → classification + confidence |
| Pair mismatch (lift vs export) | Implemented |
| Reports + markup PDF | Implemented (markup falls back to page renders for non-PDF sources) |
| Hybrid retrieval + grounded chat | Implemented — citation retry, spatial re-rank, no silent ID substitution |
| Traces, structured logs, LLM telemetry, metrics | Implemented |
| Eval harness with gates | Implemented (`fail_on_gate: true`) |
| **Regression comparison across runs** | `make eval-compare` vs committed `eval/baseline.json` |
| **Cost/latency budget analysis** | In every scorecard, with per-stage p50/p95 vs budget |
| React + FastAPI + Docker | Implemented |

All three formats in the brief run end-to-end. **DWG is the one thing that does not**, and that is a licensing reality rather than a shortcut — see *Known limits*.

## Current scorecard

Reproduce with `make eval` (numbers from `artifacts/eval/latest.json`):

| Metric | Value |
|---|---|
| Native delta F1 | **0.923** |
| Scanned delta F1 | **0.923** |
| CAD delta F1 | **0.833** |
| Pair mismatch accuracy | **1.00** |
| Retrieval recall@5 | **1.00** |
| Chat fact accuracy | **1.00** (15 Q&A) |
| Citation validity | **1.00** |
| Unsupported refusal accuracy | **1.00** |
| All gates passed | **true** |

The scorecard also prints a **failure table** enumerating every false positive, missed change, and chat miss — not just gate breaches. See *Where it fails*.

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .\.venv\Scripts\pip
python scripts/verify.py               # samples + lint + types + tests + frontend + eval
```

`scripts/verify.py` is the one command that works everywhere — it runs the same chain as `make verify` through the current interpreter, because `make` is not present on a stock Windows install and that is where this gets reviewed. `--quick` skips the frontend and eval steps. Full run is ~45s.

No system binaries are required either: OCR runs on pip-installed ONNX weights, CAD on pure-Python `ezdxf`.

With `make` available:

```bash
make samples          # all three sample pairs + registry
make test lint eval
make demo             # builds React, serves http://127.0.0.1:8000
```

## Docker

```bash
docker compose up --build
# http://localhost:8000
```

Multi-stage: Node builds React, the Python image installs both OCR backends and CAD support, and pre-warms the ONNX models so the first scanned request is not slow. Runs unprivileged as uid 1000 and honours `PORT`. Private P&IDs are never copied into the image.

Verified: 2.01 GB image, healthy in ~10s, all three formats reporting available on `/api/health`, and a full CAD pair + grounded chat exercised inside the container. Hosted deployment options and their resource floors are in [DEPLOY.md](DEPLOY.md) — note that a 512 MB free tier will OOM.

## Verification

```bash
python scripts/verify.py    # cross-platform; no make required
make verify                 # identical chain via the Makefile
```

Both run: samples → format-check → ruff → mypy → pytest → frontend lint/test/build → eval → eval-compare, and fail fast with the step name.

## Architecture

```text
PID A/B → resolver → content-sniffing detector
       → native_pdf | scanned_pdf | dxf   (one adapter interface)
       → canonical JSON → pair compatibility → registration
       → Hungarian matching + residual visual geometry → delta.json
       → reports + markup → retrieval index → grounded chat
       → React UI via FastAPI /api/*
```

The **canonical representation** is the load-bearing seam: normalized top-left bounding boxes, extracted identifiers, kind classification, and an extraction confidence. Nothing downstream of it knows whether a page came from a vector PDF, a raster scan, or a CAD modelspace. Adding the CAD adapter required no change to the delta engine, retrieval, or chat — which is the closest thing to proof the abstraction holds.

### Format detection

Content-sniffed, not extension-based: DWG by `AC10xx` magic, DXF by its `SECTION` header or binary sentinel, PDF routed to native vs scanned by text density, vector count, and image coverage.

### OCR backend seam

`config.ocr.backend` selects `auto` | `rapidocr` | `tesseract`. `auto` walks `backend_priority` and takes the first available; an explicit name **never** silently falls back, because a comparison between engines is worthless if you cannot tell which one ran. The chosen backend is recorded in the canonical metadata, per-element attributes, and the scorecard.

Backends declare a `granularity`. Tesseract emits words and the adapter groups them into lines; RapidOCR emits lines already, and re-grouping them was actively harmful — see below.

### Where the LLM is, and is not

The delta engine is fully deterministic: same inputs, same `delta.json`. The LLM is confined to answer synthesis in chat, and every citation is validated against the retrieval index before an answer is returned. Default `LLM_PROVIDER=extractive` is a deterministic no-key baseline; set `LLM_PROVIDER`/`LLM_MODEL` plus credentials for the LiteLLM path (`pip install '.[llm]'`). Token cost reports as **unavailable**, never `0.00`, unless a provider returns a real cost figure.

## Observability

Every run writes to `artifacts/runs/<request_id>/`: `trace.json` (spans with per-stage timings, chat spans correlated to the parent run), `events.jsonl` (structured logs with correlation ids), `llm_calls.jsonl` (model, tokens, cost, content only when `capture_content` is on), `metrics.json`, and `errors.jsonl`. `GET /api/health` reports which format capabilities are actually usable, so a degraded deployment is visible without submitting a document.

## Evaluation

`make eval` runs four labeled cases (native, scanned, CAD, pair-mismatch) covering delta P/R/F1 against ground truth, retrieval recall@5, chat fact accuracy, citation validity, and refusal accuracy, then applies gates and exits non-zero on breach.

**Regression detection.** `make eval-compare` diffs the latest scorecard against the committed `eval/baseline.json`, direction-aware per metric with per-metric tolerances, and exits non-zero on regression. It also flags a metric that *disappeared* — a case that silently stopped running otherwise looks like a clean scorecard. `make eval-baseline` promotes a reviewed run.

**Budgets.** Each scorecard carries per-stage p50/p95/max against declared budgets. This is not decoration — it found a real performance bug. Markup overlay was running at **9.7s p95** against a 2s budget. My first guess was preview rasterization; measuring showed that took 0.05s. The actual cause was per-change PDF writes: `new_shape()`/`commit()` and `insert_text()` each emit their own content stream, ~15ms apiece, invisible on a 6-change pair and ruinous on the 624-change mismatch pair. Batching boxes by style and routing labels through a single `TextWriter` took it to **296ms p95**, a 33× improvement, and end-to-end p95 from 13.4s to 6.5s.

### Where it fails

Honest, current, and reproduced by `make eval`:

| Failure | Cause | Status |
|---|---|---|
| Scanned + CAD each report 1 FP | An added branch is reported as *two* findings — the geometry region and its valve label (`HV-205` / `HV-305`) — where ground truth labels the whole thing as one region | **Labeling granularity, not a detection defect.** The system arguably has this right; I left the ground truth alone rather than widen a label to improve a number |
| CAD 1 FN | Same branch: the matched finding's centroid sits outside the 0.08 location tolerance | **Open.** Scoring should accept either the region or its label |
| Native 1 FP | One low-confidence residual region | Open, low impact |
| DWG not end-to-end | No usable open-source DWG reader | **By design** — converter seam, explicit error |
| Dense multi-sheet CAD | Registration/layout assumes one sheet per page | Rejects low-confidence registration rather than guessing |

Three false-positive sources were found and fixed, all worth naming because all three were silent:

- **Residual ink reported twice.** The delta engine recorded which regions a semantic change explained, so the pixel-residual pass could skip them — but it recorded only the *new* position of a matched element and nothing at all for removals. A moved transmitter left its vacated region unexplained, and every removed element was reported once semantically and again as residual geometry. Suppression also used IoU, which cannot see containment: two ink blobs inside an added `NOTE 12` line have IoU far below any usable threshold despite being the same ink. Now both sides of a match are recorded, removals record their region, and suppression uses symmetric containment. **Scanned F1 0.667 → 0.923**, precision 0.50 → 0.857, with native and CAD unchanged — verified by `make eval-compare`, which is what that tool is for.

- **Unstable line grouping.** Fixed-height bucketing of OCR words put identical content in different groups on Rev A and Rev B, manufacturing 4 phantom changes from one drawing region. Replaced with vertical-overlap clustering plus a horizontal-gap break, and line-granular engines are no longer re-grouped at all. Scanned F1 0.52 → 0.60.
- **Whitespace-only OCR differences.** `NOTE 10:See package` vs `NOTE10:Seepackage` is the same ink; word segmentation is the recognizer's artifact, not the drawing's. Suppressed for OCR-sourced elements only, and only when whitespace *placement* differs — any glyph or digit change still reports, so `12000 → 12500` is unaffected. Scanned F1 0.60 → **0.667**.

A retrieval bug was also fixed: delta records carry their *neighbours'* tags, so in a "what changed near X" query each change anchored on itself and scored perfect proximity. Anchors are now restricted to document elements. Chat fact accuracy 0.93 → 1.00.

## Security / privacy

- `request_id` matches `[A-Za-z0-9_-]{1,64}`; path traversal rejected
- API responses expose URL paths only, never host filesystem paths
- HTML reports escape extracted text
- LLM content capture is off by default; chat logs store hashes and lengths instead
- Private inputs, `.env`, artifacts, and planning files are gitignored and dockerignored

## Deliberate cuts

- **No vector embeddings.** Lexical hybrid (exact tag + word/char TF-IDF + RRF) plus spatial re-rank hits recall@5 = 1.0 on this corpus. An embedding index would add a model dependency and latency for no measured gain — but it is the first thing to revisit on a larger corpus, where tag-name overlap stops being discriminative.
- **No LLM in the delta path.** Alignment is a matching problem with exact coordinates available; a deterministic solver is reproducible and free. The LLM earns its place in answer synthesis, not change detection.
- **Single-sheet assumption** in registration and grid estimation.
- **No multi-user auth** — local demo boundary.

## What I would do next

1. **Region-level ground truth for geometry changes.** The remaining FP/FN on both the scanned and CAD cases is one problem: an added branch is a *region* in the labels but the system reports both the region and its valve label. Scoring should accept either, or the labels should enumerate both. This is worth doing before chasing any more matcher accuracy, because right now the metric penalises correct behaviour.
2. **Residual stability across registration jitter.** Suppression is now correct for explained ink, but the threshold on unexplained residual is still fixed. A stability check across a jittered re-registration would separate real geometry change from raster noise on harder scans than these fixtures.
3. **500-sheet scale.** Everything is currently per-page and in-memory. That means sheet-level partitioning, a persistent index, and matching restricted to candidate blocks rather than a full cost matrix — the `max_pair_comparisons` guard already exists and would trip immediately.
4. **Validate the judge.** Chat scoring is deterministic string/citation checking, which is honest but shallow. An LLM judge needs its own labeled agreement set before I would trust it.
5. **Real DWG.** Bundle the ODA converter in the image where licensing permits, and add a conversion-fidelity check comparing entity counts before and after.

## CLI

```bash
delta-chat run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B     # native
delta-chat run-pair --pid-a PID-CAD-A --pid-b PID-CAD-B     # CAD
delta-chat chat --run-id <id> -q "What changed near 26-PIT-9062?"
python -m eval.run
python -m eval.compare
```

## Sample provenance

See [`data/samples/README.md`](data/samples/README.md). All three pairs are synthetic and drawn independently per revision — no white-out over hidden text, no edited-file residue. The CAD pair carries the same six-change edit set as the PDF pairs on purpose, so `cad_delta_f1` vs `scanned_delta_f1` isolates extraction error from matching error.
