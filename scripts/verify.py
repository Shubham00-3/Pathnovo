"""Cross-platform equivalent of `make verify`.

The Makefile is the canonical entry point, but `make` is not present on a stock
Windows install -- which is exactly where this project gets reviewed. This runs
the same chain through the current interpreter so there is always one command
that works everywhere.

    python scripts/verify.py            # full chain
    python scripts/verify.py --quick    # skip the frontend and the eval pass
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SAMPLE_GENERATORS = [
    "make_synthetic_pid_pair",
    "make_secondary_pid_pair",
    "make_scanned_pair",
    "make_cad_pair",
    "build_eval_dataset",
]


class StepFailed(Exception):
    def __init__(self, name: str, code: int) -> None:
        super().__init__(f"{name} failed with exit code {code}")
        self.name = name
        self.code = code


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Match the Makefile: src on the path for the package, "." for eval/scripts.
    env["PYTHONPATH"] = os.pathsep.join(["src", "."])
    return env


def run(name: str, args: list[str], *, cwd: Path | None = None) -> float:
    print(f"\n=== {name} ===", flush=True)
    started = time.time()
    result = subprocess.run(args, cwd=str(cwd or ROOT), env=_env())
    elapsed = time.time() - started
    if result.returncode != 0:
        raise StepFailed(name, result.returncode)
    print(f"--- {name} ok ({elapsed:.1f}s)", flush=True)
    return elapsed


def python_module(name: str, module: str, *args: str) -> float:
    return run(name, [sys.executable, "-m", module, *args])


def generate_samples() -> float:
    total = 0.0
    for generator in SAMPLE_GENERATORS:
        total += run(
            f"samples:{generator}",
            [sys.executable, "-c", f"from scripts.{generator} import main; main()"],
        )
    return total


def frontend_steps() -> None:
    npm = shutil.which("npm")
    frontend = ROOT / "frontend"
    if not npm or not frontend.exists():
        print("\n=== frontend: SKIPPED (npm not found) ===", flush=True)
        return
    if not (frontend / "node_modules").exists():
        run("frontend:install", [npm, "install"], cwd=frontend)
    for script in ("lint", "test", "build"):
        run(f"frontend:{script}", [npm, "run", script], cwd=frontend)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the full verification chain.")
    parser.add_argument("--quick", action="store_true", help="Skip frontend and eval steps.")
    args = parser.parse_args(argv)

    started = time.time()
    try:
        generate_samples()
        python_module(
            "format-check", "ruff", "format", "--check", "src", "tests", "scripts", "eval"
        )
        python_module("lint", "ruff", "check", "src", "tests", "scripts", "eval")
        python_module("typecheck", "mypy", "src", "scripts", "eval")
        python_module("test", "pytest", "-q")
        if not args.quick:
            frontend_steps()
            python_module("eval", "eval.run")
            python_module("eval-compare", "eval.compare")
    except StepFailed as exc:
        print(f"\nVERIFY FAILED at '{exc.name}' (exit {exc.code})", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        print("\nVERIFY INTERRUPTED", file=sys.stderr)
        return 130

    print(f"\nVERIFY OK ({time.time() - started:.1f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
