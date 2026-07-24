# DEMO.md — 2–4 minute walkthrough

## Prerequisites

```bash
uv sync --all-extras
# or: pip install -e ".[dev]"
make samples   # or uv run python -c "from scripts.make_synthetic_pid_pair import main; main()" ...
```

Optional OCR (scanned path): install Tesseract and ensure it is on `PATH`.

## Script

1. **Start demo UI**
   ```bash
   make demo
   # uv run streamlit run src/delta_chat/ui/app.py --server.port 8501
   ```
   Open http://localhost:8501

2. **Pair setup** — select `PID-SYN-A` and `PID-SYN-B`, mismatch mode `warn`, click **Run comparison**.

3. **Delta tab** — show the structured changes (setpoint, duty, note, line tag, move, geometry). Open `report.md` / filter by confidence.

4. **Markup tab** — download `markup.pdf` (green add / red remove / amber modify-move).

5. **Chat tab** — ask:
   - `What changed near 26-PIT-9062?`
   - `What is the duty in revision B?`
   - `Did the motor vendor change?` (unsupported / no evidence)
   Expand citations (`D:…`, `A:…`, `B:…`).

6. **Observability tab** — show `trace.json` spans, `metrics.json`, `events.jsonl`, `llm_calls.jsonl`.

7. **Evaluation** — in another terminal:
   ```bash
   make eval
   ```
   Show scorecard F1, mismatch accuracy, citation validity, refusal accuracy.

8. **Mismatch guard** — run `PID-LIFT` vs `PID-EXPORT` (or fixtures) in `warn` mode; show compatibility warning (not a silent revision narrative).

9. **Close** — DWG is a real adapter seam with a visible actionable failure; not end-to-end without a converter.

## CLI alternative (no UI)

```bash
uv run delta-chat run-pair --pid-a PID-SYN-A --pid-b PID-SYN-B
uv run delta-chat chat --run-id <request_id> -q "Summarize only high-confidence changes."
uv run python -m eval.run
```
