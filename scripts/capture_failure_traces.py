"""Trigger real failures and commit their traces.

Failure visibility is easy to claim and hard to evidence: the traces live under
`artifacts/`, which is gitignored, so a reviewer who clones the repo sees none
of it. This provokes genuine failures through the normal pipeline -- no mocks,
no hand-written JSON -- and writes the resulting traces to a committed folder.

    python scripts/capture_failure_traces.py

Each case answers "what does this system do when X goes wrong", where X is a
thing that actually happens: the wrong two documents get compared, a CAD file
arrives without a converter, or a PID does not resolve.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from delta_chat.config import load_config, project_root
from delta_chat.errors import DeltaChatError
from delta_chat.pipeline import run_pair

OUT_DIR = project_root() / "docs" / "failure-traces"


def _capture(name: str, description: str, fn) -> dict[str, Any]:
    """Run something expected to fail; record what the system left behind."""
    record: dict[str, Any] = {"case": name, "description": description}
    try:
        fn()
    except DeltaChatError as exc:
        record["raised"] = exc.__class__.__name__
        record["code"] = exc.code
        record["message"] = exc.message
        record["details"] = exc.details
        record["outcome"] = "typed error with actionable details"
    except Exception as exc:  # noqa: BLE001
        record["raised"] = exc.__class__.__name__
        record["message"] = str(exc)[:400]
        record["outcome"] = "untyped error (should be typed - worth fixing)"
    else:
        record["outcome"] = "UNEXPECTED: no error raised"
    return record


def _copy_run_artifacts(request_id: str, dest: Path) -> list[str]:
    """Pull the trace/logs the failed run wrote before it aborted."""
    run_dir = project_root() / "artifacts" / "runs" / request_id
    if not run_dir.exists():
        return []
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("trace.json", "errors.jsonl", "events.jsonl", "metrics.json"):
        src = run_dir / name
        if src.exists():
            shutil.copy2(src, dest / name)
            copied.append(name)
    return copied


def main() -> dict[str, Any]:
    cfg = load_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []

    # 1. The wrong two documents. Lift vs export are different drawings, not
    #    revisions of one. In strict mode this must abort rather than emit a
    #    600-change "delta" that means nothing.
    rid = "failcase-pair-mismatch"
    case = _capture(
        "pair_mismatch_strict",
        "Two unrelated drawings compared in strict mode",
        lambda: run_pair(
            "PID-LIFT", "PID-EXPORT", config=cfg, mismatch_mode="strict", request_id=rid
        ),
    )
    case["artifacts"] = _copy_run_artifacts(rid, OUT_DIR / "pair_mismatch_strict")
    cases.append(case)

    # 2. A CAD file with no converter available. The seam is real, so the error
    #    has to name the missing dependency and the config key that fixes it.
    dwg = project_root() / "artifacts" / "_synthetic.dwg"
    dwg.parent.mkdir(parents=True, exist_ok=True)
    dwg.write_bytes(b"AC1032" + b"\0" * 128)
    registry_path = project_root() / cfg.get("paths", {}).get("registry", "data/registry.json")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    original = registry.copy()
    registry["PID-DWG-NOCONV"] = {
        "underlying_document_id": "DOC-CAD-BOOSTER",
        "revision_label": "C",
        "path": str(dwg.relative_to(project_root())).replace("\\", "/"),
        "media_type": "image/vnd.dwg",
        "display_name": "Synthetic DWG (no converter)",
    }
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    try:
        rid2 = "failcase-dwg-no-converter"
        case2 = _capture(
            "dwg_without_converter",
            "DWG input with no ODA/dwg2dxf converter configured",
            lambda: run_pair("PID-CAD-A", "PID-DWG-NOCONV", config=cfg, request_id=rid2),
        )
        case2["artifacts"] = _copy_run_artifacts(rid2, OUT_DIR / "dwg_without_converter")
        cases.append(case2)
    finally:
        registry_path.write_text(json.dumps(original, indent=2), encoding="utf-8")
        dwg.unlink(missing_ok=True)

    # 3. An unresolvable PID -- the cheapest failure, and the one most likely to
    #    be swallowed into a generic 500 by a careless handler.
    rid3 = "failcase-pid-not-found"
    case3 = _capture(
        "pid_not_found",
        "A PID that is not in the registry",
        lambda: run_pair("PID-DOES-NOT-EXIST", "PID-SYN-B", config=cfg, request_id=rid3),
    )
    case3["artifacts"] = _copy_run_artifacts(rid3, OUT_DIR / "pid_not_found")
    cases.append(case3)

    summary = {
        "generated_by": "scripts/capture_failure_traces.py",
        "note": (
            "Produced by running the real pipeline. Each case aborted with a typed "
            "error; the trace shows which stage failed and errors.jsonl records why."
        ),
        "cases": cases,
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )

    for c in cases:
        print(f"{c['case']:26s} {c.get('raised', '-'):24s} {c['outcome']}")
    print(f"\nWrote {OUT_DIR}")
    return summary


if __name__ == "__main__":
    main()
