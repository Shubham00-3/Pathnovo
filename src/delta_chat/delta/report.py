"""Markdown/HTML delta report writers."""

from __future__ import annotations

import html
from pathlib import Path

from delta_chat.canonical.serialization import dump_json
from delta_chat.delta.models import DeltaReport


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def write_delta_json(report: DeltaReport, path: Path, *, strip_timestamps: bool = False) -> None:
    data = report.model_dump(mode="json")
    if strip_timestamps:
        data.pop("generated_at", None)
    dump_json(path, data)


def render_markdown(report: DeltaReport) -> str:
    lines = [
        f"# Delta Report: {report.pid_a} → {report.pid_b}",
        "",
        f"- Delta ID: `{report.delta_id}`",
        f"- Config hash: `{report.config_hash}`",
        f"- Generated: {report.generated_at}",
        "",
        "## Pair compatibility",
        "",
        f"- Compatible: **{report.pair_compatibility.get('compatible')}**",
        f"- Score: {report.pair_compatibility.get('score')} (threshold {report.pair_compatibility.get('threshold')})",
        f"- Mode: {report.pair_compatibility.get('mode')}",
    ]
    if report.pair_compatibility.get("warning"):
        lines.append(f"- **Warning:** {report.pair_compatibility.get('warning')}")
    for r in report.pair_compatibility.get("reasons") or []:
        lines.append(f"  - {r}")
    if report.warnings:
        lines += ["", "## Warnings", ""]
        for w in report.warnings:
            lines.append(f"- {w}")

    lines += [
        "",
        "## Executive summary",
        "",
        f"- Total changes: **{report.summary.get('total_changes', 0)}**",
        f"- By type: {report.summary.get('by_change_type', {})}",
        f"- By confidence: {report.summary.get('by_confidence_band', {})}",
        f"- Suppressed noise/unchanged matches: {report.summary.get('suppressed_unchanged_or_noise', 0)}",
        "",
        "## High-confidence changes",
        "",
    ]
    high = [c for c in report.changes if c.confidence_band == "high"]
    if not high:
        lines.append("_None_")
    for c in high:
        lines.append(
            f"- `{c.delta_item_id}` **{c.change_type}** ({c.entity_type}): {c.deterministic_description}"
        )

    lines += ["", "## All changes", ""]
    for c in report.changes:
        lines.append(f"### {c.delta_item_id} — {c.change_type} / {c.entity_type}")
        lines.append(f"- {c.deterministic_description}")
        lines.append(f"- Confidence: {c.confidence} ({c.confidence_band})")
        if c.before is not None:
            lines.append(f"- Before: `{c.before}`")
        if c.after is not None:
            lines.append(f"- After: `{c.after}`")
        if c.region:
            lines.append(f"- Region: `{c.region}`")
        lines.append("")

    low = [c for c in report.changes if c.confidence_band == "low"]
    lines += ["## Low-confidence review queue", ""]
    if not low:
        lines.append("_None_")
    for c in low:
        lines.append(f"- `{c.delta_item_id}`: {c.deterministic_description}")

    lines += [
        "",
        "## Metrics",
        "",
        f"```json\n{report.metrics}\n```",
        "",
    ]
    return "\n".join(lines)


def render_html(report: DeltaReport) -> str:
    rows = []
    for c in report.changes:
        rows.append(
            "<tr>"
            f"<td>{_esc(c.delta_item_id)}</td>"
            f"<td>{_esc(c.change_type)}</td>"
            f"<td>{_esc(c.entity_type)}</td>"
            f"<td>{_esc(c.confidence_band)}</td>"
            f"<td>{_esc(c.deterministic_description)}</td>"
            f"<td>{_esc((c.before or '')[:80])}</td>"
            f"<td>{_esc((c.after or '')[:80])}</td>"
            "</tr>"
        )
    warn = "".join(f"<li>{_esc(w)}</li>" for w in report.warnings)
    compat = report.pair_compatibility
    warn_block = (
        f"<p class='warn'><b>Warning:</b> {_esc(compat.get('warning'))}</p>"
        if compat.get("warning")
        else ""
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Delta {_esc(report.pid_a)} → {_esc(report.pid_b)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;background:#0f1115;color:#e7ecf3}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #2a323e;padding:8px;text-align:left;vertical-align:top}}
th{{background:#1c222b}}
.card{{background:#161a21;border:1px solid #2a323e;border-radius:10px;padding:16px;margin:12px 0}}
.warn{{color:#f5a524}}
</style></head><body>
<h1>Delta Report: {_esc(report.pid_a)} → {_esc(report.pid_b)}</h1>
<div class="card">
<p><b>Delta ID:</b> {_esc(report.delta_id)}<br>
<b>Compatible:</b> {_esc(compat.get("compatible"))} (score {_esc(compat.get("score"))})<br>
<b>Total changes:</b> {_esc(report.summary.get("total_changes"))}</p>
{warn_block}
<ul>{warn}</ul>
</div>
<table>
<thead><tr><th>ID</th><th>Type</th><th>Entity</th><th>Band</th><th>Description</th><th>Before</th><th>After</th></tr></thead>
<tbody>
{"".join(rows)}
</tbody></table>
</body></html>
"""


def write_reports(report: DeltaReport, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "delta.json"
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"
    write_delta_json(report, json_path)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return {"delta_json": str(json_path), "report_md": str(md_path), "report_html": str(html_path)}
