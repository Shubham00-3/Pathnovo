"""Typer CLI for delta-chat."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from delta_chat import __version__
from delta_chat.config import load_config, project_root
from delta_chat.pipeline import chat_on_run, run_pair

app = typer.Typer(help="Document delta engine with grounded chat", no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Document Delta & Grounded Chat CLI."""


@app.command("version")
def version() -> None:
    console.print(__version__)


@app.command("list-pids")
def list_pids() -> None:
    from delta_chat.pid.local_registry import LocalRegistryResolver

    cfg = load_config()
    r = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))
    for pid in r.list_pids():
        console.print(pid)


@app.command("run-pair")
def run_pair_cmd(
    pid_a: str = typer.Option(..., "--pid-a"),
    pid_b: str = typer.Option(..., "--pid-b"),
    mode: str | None = typer.Option(None, "--mismatch-mode", help="strict|warn|force"),
    request_id: str | None = typer.Option(None, "--request-id"),
) -> None:
    """Ingest two PIDs, compute delta, write reports and traces."""
    result = run_pair(pid_a, pid_b, mismatch_mode=mode, request_id=request_id)
    table = Table(title="Pair run complete")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("request_id", result["request_id"])
    table.add_row("run_dir", result["run_dir"])
    table.add_row("changes", str(result["delta"]["summary"].get("total_changes")))
    table.add_row(
        "compatible",
        str(result["delta"]["pair_compatibility"].get("compatible")),
    )
    console.print(table)
    console.print_json(json.dumps({k: result["paths"][k] for k in result["paths"]}))


@app.command("chat")
def chat_cmd(
    run_id: str = typer.Option(..., "--run-id"),
    question: str = typer.Option(..., "--question", "-q"),
) -> None:
    """Ask a grounded question against an existing pair run."""
    from delta_chat.canonical.models import DocumentRevision
    from delta_chat.delta.models import DeltaReport
    from delta_chat.retrieval.records import RetrievalRecord

    root = project_root()
    run_dir = root / "artifacts" / "runs" / run_id
    if not run_dir.exists():
        raise typer.BadParameter(f"Run not found: {run_dir}")
    delta = DeltaReport.model_validate_json((run_dir / "delta.json").read_text(encoding="utf-8"))
    doc_a = DocumentRevision.model_validate_json(
        (run_dir / "canonical_a.json").read_text(encoding="utf-8")
    )
    doc_b = DocumentRevision.model_validate_json(
        (run_dir / "canonical_b.json").read_text(encoding="utf-8")
    )
    records_raw = json.loads((run_dir / "retrieval_records.json").read_text(encoding="utf-8"))
    records = [RetrievalRecord.model_validate(r) for r in records_raw]
    cfg = load_config()
    from delta_chat.observability.llm_telemetry import LLMTelemetry

    telemetry = LLMTelemetry(run_dir / "llm_calls.jsonl", capture_content=True)
    payload = {
        "report": delta,
        "records": records,
        "config": cfg,
        "run_dir": str(run_dir),
        "doc_a": doc_a,
        "doc_b": doc_b,
        "telemetry": telemetry,
    }
    answer = chat_on_run(payload, question)
    console.print_json(json.dumps(answer))


@app.command("eval")
def eval_cmd(
    dataset: str = typer.Option("eval/datasets/v1.yaml", "--dataset"),
) -> None:
    from eval.run import run_eval

    scorecard = run_eval(dataset_path=dataset)
    console.print_json(json.dumps(scorecard.get("summary", scorecard)))


@app.command("samples")
def samples_cmd(seed: int = typer.Option(42, "--seed")) -> None:
    from scripts.make_scanned_pair import main as make_scan
    from scripts.make_synthetic_pid_pair import main as make_syn

    make_syn(seed=seed)
    make_scan(seed=seed)
    console.print("[green]Samples generated.[/green]")


if __name__ == "__main__":
    app()
