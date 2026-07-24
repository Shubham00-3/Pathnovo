# DEMO.md — React walkthrough (≤4 minutes)

## Start (pick one)

**Docker (preferred single command):**

```bash
docker compose up --build
# open http://localhost:8000
```

**Local API + built React:**

```powershell
$env:PYTHONPATH = "src;."
cd frontend; npm run build; cd ..
python -m uvicorn delta_chat.api:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
```

## Script

1. **Pair setup** — `PID-SYN-A` / `PID-SYN-B`, mode `warn` → **Run comparison**.
2. **Delta** — show controlled changes (HH 245→250, duty, NOTE 12, removed line tag, moved PT, geometry branch). Open `delta.json` / `report.md`.
3. **Markup** — download `markup.pdf`; view page renders.
4. **Chat**
   - `What changed near 26-PIT-9062?` → setpoint 250 + citations
   - `Did the motor vendor change?` → unsupported refusal (no vendor evidence)
   - Expand citation IDs (`D:…` / `A:…` / `B:…`)
5. **Observability** — after chat, refresh: `trace.json` includes `retrieval.query`, `llm.answer` / deterministic answer, `citation.validate`, `answer`; `metrics.json` shows cumulative chat/LLM counts.
6. **Evaluation** — `python -m eval.run`; open Evaluation tab scorecard (native F1 numeric; scanned numeric only when Tesseract present).
7. **Mismatch** — `PID-LIFT` / `PID-EXPORT` warn mode → compatibility warning; strict mode → typed error.
8. **Close** — DWG is a real seam, not end-to-end without a converter.

## Notes

- Default extractive provider is a deterministic baseline, not a cloud LLM evaluation.
- Lift/export are different documents for mismatch testing only.
