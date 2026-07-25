# Failure traces

Real failures, captured by running the pipeline. Regenerate with:

```bash
python scripts/capture_failure_traces.py
```

Run artifacts normally live under gitignored `artifacts/`, so a fresh clone
would show no evidence that failures are handled at all. These three are
committed so the behaviour is inspectable without running anything.

Nothing here is hand-written. Each case aborted through the normal code path.

| Case | Raised | What it demonstrates |
|---|---|---|
| `pair_mismatch_strict/` | `PairMismatchError` | Two unrelated drawings compared in strict mode. Refuses rather than emitting a 600-change "delta" that means nothing |
| `dwg_without_converter/` | `UnsupportedFormatError` | The CAD seam is real: the error names the missing dependency and the config key that fixes it |
| `pid_not_found/` | `PidNotFoundError` | The cheapest failure, and the one most often swallowed into a generic 500 |

## What to look at

**`trace.json`** — the failing stage carries `"status": "error"`, and the spans
before it still record their timings, so you can see how far the request got.
In `pair_mismatch_strict`, both `ingest.a` and `ingest.b` complete (613ms,
290 elements) and `delta.engine` is where it stops.

**`errors.jsonl`** — a typed record with a stable `code` and details a person
can act on. The mismatch case does not just say "incompatible"; it gives the
score, the threshold, and the reasons:

```json
{
  "error_type": "PairMismatchError",
  "code": "pair_mismatch",
  "details": {
    "score": 0.2,
    "threshold": 0.65,
    "reasons": ["underlying_document_id differs",
                "stable-token overlap is moderate",
                "primary equipment tag differs"],
    "equipment_a": ["26-KA-901"],
    "equipment_b": ["26-KA-902"]
  }
}
```

**`metrics.json`** — `errors` is incremented by type, so a failure is countable
and not only greppable.

**`events.jsonl`** — structured log lines carrying the same `request_id`, so a
failure can be correlated across all four files.

## Why these three

They are the failures that actually occur in this domain: the wrong two
documents get compared, a CAD file arrives without the proprietary converter,
or an identifier does not resolve. Each is a different layer — semantic
precondition, missing capability, and resolution — so together they show the
error path is not just one try/except in one place.

The mismatch case is the interesting one. It is not an exception in the
plumbing sense; the pipeline could happily diff those two drawings and return
624 changes. Refusing is a judgment the system makes about whether the question
is meaningful, and `strict` vs `warn` is how a caller chooses that policy.
