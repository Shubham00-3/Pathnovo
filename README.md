# Document Delta & Grounded Chat

Format-agnostic pipeline that resolves two **PIDs** (document revision identifiers), normalizes them into a **canonical representation**, computes a **deterministic, coordinate-aware delta**, writes JSON/Markdown/HTML reports (plus optional markup), and answers questions with **validated citations** over Rev A, Rev B, and the delta report.

**Implemented end to end:** native PDF, scanned PDF (OCR), structured delta, reports, markup overlay, hybrid retrieval, grounded chat, observability artifacts, evaluation harness, Streamlit UI, CLI.

**Partial:** DWG (detection + adapter seam + visible failure only). Optional cloud LLM via LiteLLM when keys are set (default is deterministic extractive provider).

**Not claimed:** full CAD topology, multi-user auth, production DWG conversion without an external converter.

## Quick start

```bash
# Python 3.11+ recommended (3.12/3.13 also used in development)
uv sync --all-extras
# Windows without Make:
uv run python -c "from scripts.make_synthetic_pid_pair import main; main()"
uv run python -c "from scripts.make_scanned_pair import main; main()"
uv run python -c "from scripts.build_eval_dataset import main; main()"
uv run pytest -q
uv run python -m eval.run
uv run streamlit run src/delta_chat/ui/app.py --server.port 8501
```

With Make (Linux/macOS/Docker, or Make on Windows):

```bash
make samples
make lint
make test
make eval
make demo
```

Demo URL: **http://localhost:8501**

### uv / CLI equivalents

```bash
uv run delta-chat run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B
uv run delta-chat chat --run-id <request_id> -q "What changed near 26-PIT-9062?"
uv run delta-chat eval
uv run delta-chat list-pids
```

### Docker

```bash
docker compose up --build
# http://localhost:8501
```

## Architecture

```text
PID A/B → resolver → format detector → native|scanned|dwg adapter
       → canonical JSON → pair compatibility → registration
       → semantic match + residual visual geometry → delta.json
       → reports + markup → retrieval index → grounded chat
```

Observability wraps every stage (`events.jsonl`, `trace.json`, `metrics.json`, `llm_calls.jsonl`).

## Canonical representation

Versioned Pydantic models (`DocumentRevision` / `CanonicalElement`) store:

- normalized top-left bboxes in `[0, 1]`
- element kinds (text, tags, notes, table cells, geometry clusters, …)
- identifiers, extraction confidence, source refs
- adapter provenance (name, version, source SHA-256)

Adapters never leak into the delta engine or chat layer.

## Delta engine

1. **Pair compatibility** — underlying document id, token overlap, equipment tags  
2. **Page alignment** — one-to-one sheet matching  
3. **Registration** — ORB+RANSAC, ECC fallback; low quality suppresses visual residual  
4. **Semantic matching** — spatial candidates, multi-feature score, Hungarian assignment  
5. **Classification** — added / removed / modified / moved / moved_modified  
6. **Visual residual** — warped absdiff → morphology → components → deduped geometry regions  

The **LLM is never the source of truth for what changed**. Deterministic descriptions are primary.

## Grounded chat

- Hybrid retrieval: exact tag boost + word TF-IDF + char n-grams + RRF  
- Deterministic answers for counts / high-confidence lists / delta item ids  
- LLM (extractive by default, LiteLLM optional) only explains from retrieved evidence  
- Citations validated against retrieved source IDs; fabrications rejected  

## Observability

Each pair run writes under `artifacts/runs/<request_id>/`:

| Artifact | Purpose |
|----------|---------|
| `canonical_a.json` / `canonical_b.json` | Normalized revisions |
| `delta.json` | Structured delta |
| `report.md` / `report.html` | Human reports |
| `markup.pdf` | Redline-style overlay |
| `events.jsonl` | Structured logs |
| `trace.json` | Stage spans + timings |
| `metrics.json` | Counts, latency, LLM totals |
| `llm_calls.jsonl` | Prompt/response telemetry (optional redact) |
| `errors.jsonl` | Typed failures |

## Evaluation

```bash
make eval
# uv run python -m eval.run
```

Cases:

1. Controlled **native** synthetic revision pair  
2. **Scanned** variant (skipped cleanly if Tesseract missing)  
3. **Lift/export mismatch** (private PDFs or local fixtures)  

Scorecard: `artifacts/eval/<run_id>/scorecard.json` + `scorecard.md`  
Metrics include delta P/R/F1 (bipartite GT match), mismatch accuracy, citation validity, refusal accuracy. **Scores are measured, never hardcoded.**

## Sample data & provenance

See [`data/samples/README.md`](data/samples/README.md).

Supplied lift/export AutoCAD P&IDs (if present under `data/private_inputs/`) are **different equipment** (`26-KA-901` vs `26-KA-902`) and are used only as a **pair-mismatch** case—not as a normal revision delta.

## Design decisions

| Decision | Why |
|----------|-----|
| Canonical intermediate model | One delta/chat path for all formats |
| Deterministic delta first | Auditable, regression-friendly, no invented changes |
| Registration + semantic match + residual geometry | Text alone is too noisy on dense drawings |
| Extractive default LLM | Reproducible eval without API keys |
| PID registry (not raw paths) as public API | Matches domain language; paths are setup |
| Strict citation validation | Prevents ungrounded answers |

## Deliberate cuts

- No full P&ID symbol ontology / topology graph  
- No multi-sheet performance engineering beyond single-page synthetic demos  
- No cloud deployment / auth  
- DWG conversion requires external tool; seam is tested, E2E is not claimed  

## Known failures / limits

| Symptom | Why | Mitigation |
|---------|-----|------------|
| OCR confuses dense tags | Digit confusion | Keep raw OCR + confidence; refuse weak evidence |
| Move+edit → remove/add | Match score drops | Anchors + looser radius for identifiers |
| Visual residual noise | Anti-alias / clouds | Morphology, area filters, dedupe vs semantic boxes |
| Large redesign breaks registration | Feature mismatch | Reject low confidence; warn; skip residual |
| DWG not E2E | No bundled converter | Visible `UnsupportedFormatError` with config hint |

## Security / privacy

- Registry paths must stay under the project root  
- Secrets only via environment (see `.env.example`)  
- Private PDFs and `artifacts/` are gitignored  
- Planning files `GROK_*.md` excluded from commits  

## What is implemented vs stubbed

| Area | Status |
|------|--------|
| Native PDF adapter | **Implemented** |
| Scanned PDF + OCR | **Implemented** (requires Tesseract) |
| DWG adapter | **Seam only** (detect + actionable fail) |
| Delta engine | **Implemented** |
| Reports + markup | **Implemented** |
| Retrieval + chat + citations | **Implemented** |
| Observability | **Implemented** |
| Eval harness | **Implemented** |
| Streamlit UI | **Implemented** (thin) |
| Embeddings / hybrid dense retrieval | **Optional off** |
| Cloud LLM | **Optional** via `LLM_PROVIDER` + `LLM_MODEL` |

## Configuration

- `config/default.yaml` — thresholds, weights, LLM, paths  
- `config/eval.yaml` — gates (informational when `fail_on_gate: false`)  
- `.env.example` — provider keys (names only)  

## Next steps

1. Calibrate match weights on a larger labeled set  
2. Optional DXF path for DWG when a converter is available  
3. Page-level parallelism and canonical caching for multi-sheet sets  
4. Stronger table structure recovery from native PDFs  
