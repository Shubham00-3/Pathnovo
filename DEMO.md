# DEMO.md — walkthrough (≤4 minutes)

## Start (pick one)

**Docker (single command):**

```bash
docker compose up --build
# open http://localhost:8000
```

**Local API + built React:**

```bash
make demo
# open http://127.0.0.1:8000
```

No system binaries needed — OCR and CAD both install via pip.

## Script

1. **Capability check** — `curl localhost:8000/api/health`. Shows `native_pdf`, `scanned_pdf`, and `cad_dxf` all true, plus which OCR backend is live. `cad_dwg` is false without a converter, and says so up front rather than at request time.

2. **Native pair** — `PID-SYN-A` / `PID-SYN-B`, mode `warn` → **Run comparison**. Six controlled changes: HH 245→250, duty 12000→12500, added NOTE 12, removed line tag, moved `26-PT-9070`, geometry branch. Open `delta.json` and `report.md`; note every change carries a type, a page + grid region, and a confidence band.

3. **Third format** — `PID-CAD-A` / `PID-CAD-B`. Same pipeline, DXF input, and adding it required no change to the delta engine, retrieval, or chat — the clearest evidence the canonical seam actually decouples format from delta. Six changes surface; scored against ground truth that is 5 true positives, 1 false positive, 1 miss (F1 0.833). The FP and the miss are the same thing: an added branch is reported as both a geometry region and its `HV-305` label, where the labels record one region.

4. **Markup** — download `markup.pdf`. For CAD there is no source PDF, so the overlay is drawn on the adapter's page render; `canvas_basis` in the trace records which was used.

5. **Chat** (grounded)
   - `What changed near 26-PIT-9080?` → HH 180→195 with citations. This exercises the spatial re-rank: the setpoint's own text never mentions the tag.
   - `What is the hydrotest pressure for the discharge spool?` → refusal, because nothing in either revision or the delta supports an answer.
   - Expand citation IDs (`D:…` / `A:…` / `B:…`) — each resolves to a retrievable source.

6. **Observability** — refresh after chat. `trace.json` spans ingest → delta → retrieval → LLM → answer with per-stage timings, chat spans correlated to the parent run; `metrics.json` has cumulative token counts; `llm_calls.jsonl` has per-call telemetry.

7. **Evaluation** — `make eval`. Scorecard prints delta P/R/F1 per format, retrieval recall@5, chat and citation metrics, gate results, the **failure table**, and the **cost/latency budget** table.

8. **Regression detection** — `make eval-compare` against the committed baseline. To see it work:

   ```bash
   python -c "import json,pathlib; p=pathlib.Path('artifacts/eval/latest.json'); d=json.loads(p.read_text()); d['native_delta_f1']=0.55; pathlib.Path('artifacts/eval/_demo.json').write_text(json.dumps(d))"
   python -m eval.compare --current artifacts/eval/_demo.json   # exits 1, names the metric
   ```

9. **Mismatch** — `PID-LIFT` / `PID-EXPORT` in warn mode → compatibility warning; strict mode → typed error. Different documents, not revisions.

10. **Close** — the honest gaps. Every format now sits at precision 0.857 or better (native 0.923 F1, scanned 0.923, CAD 0.833), and the four remaining failures are in the scorecard's failure table. Three of the four are one recurring cause: an added branch reported as both a region and its valve label, where ground truth records a single region. DWG still needs an external converter. All of it is in the README failure table.

## Notes

- The default extractive provider is a deterministic baseline, not a cloud-LLM evaluation. Cost reports as `unavailable`, never `0.00`.
- Lift/export are different documents, used only for mismatch testing.
