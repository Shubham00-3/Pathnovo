"""Thin Streamlit UI — no business logic beyond wiring."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from delta_chat.config import load_config, project_root
from delta_chat.pid.local_registry import LocalRegistryResolver
from delta_chat.pipeline import chat_on_run, run_pair

st.set_page_config(page_title="Delta Chat", layout="wide")
st.title("Document Delta & Grounded Chat")

cfg = load_config()
resolver = LocalRegistryResolver(cfg.get("paths", {}).get("registry", "data/registry.json"))
pids = resolver.list_pids()

if "run_payload" not in st.session_state:
    st.session_state.run_payload = None

tabs = st.tabs(["Pair setup", "Delta", "Markup", "Chat", "Observability", "Evaluation"])

with tabs[0]:
    c1, c2, c3 = st.columns(3)
    with c1:
        pid_a = st.selectbox("PID A", pids, index=pids.index("PID-SYN-A") if "PID-SYN-A" in pids else 0)
    with c2:
        pid_b = st.selectbox(
            "PID B",
            pids,
            index=pids.index("PID-SYN-B") if "PID-SYN-B" in pids else min(1, len(pids) - 1),
        )
    with c3:
        mode = st.selectbox("Mismatch mode", ["warn", "strict", "force"], index=0)
    if st.button("Run comparison", type="primary"):
        with st.spinner("Running pipeline..."):
            try:
                st.session_state.run_payload = run_pair(pid_a, pid_b, mismatch_mode=mode)
                st.success(f"Done. request_id={st.session_state.run_payload['request_id']}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"{type(exc).__name__}: {exc}")

    if st.session_state.run_payload:
        st.json(
            {
                "request_id": st.session_state.run_payload["request_id"],
                "run_dir": st.session_state.run_payload["run_dir"],
                "summary": st.session_state.run_payload["delta"]["summary"],
                "compatibility": st.session_state.run_payload["delta"]["pair_compatibility"],
            }
        )

payload = st.session_state.run_payload

with tabs[1]:
    if not payload:
        st.info("Run a comparison first.")
    else:
        delta = payload["delta"]
        st.subheader("Summary")
        st.write(delta.get("summary"))
        if delta.get("warnings"):
            st.warning("\n".join(delta["warnings"]))
        band = st.multiselect("Confidence", ["high", "medium", "low"], default=["high", "medium", "low"])
        types = st.multiselect(
            "Change types",
            ["added", "removed", "modified", "moved", "moved_modified"],
            default=["added", "removed", "modified", "moved", "moved_modified"],
        )
        rows = [
            c
            for c in delta.get("changes", [])
            if c.get("confidence_band") in band and c.get("change_type") in types
        ]
        st.dataframe(rows, use_container_width=True)
        md_path = Path(payload["paths"]["report_md"])
        if md_path.exists():
            st.download_button("report.md", md_path.read_text(encoding="utf-8"), file_name="report.md")
        st.download_button(
            "delta.json",
            json.dumps(delta, indent=2),
            file_name="delta.json",
        )

with tabs[2]:
    if not payload:
        st.info("Run a comparison first.")
    else:
        markup = Path(payload["paths"]["markup_pdf"])
        st.write(f"Markup PDF: `{markup}`")
        if markup.exists():
            st.download_button("Download markup.pdf", markup.read_bytes(), file_name="markup.pdf")
        renders = Path(payload["run_dir"]) / "renders"
        if renders.exists():
            imgs = sorted(renders.glob("*.png"))[:4]
            cols = st.columns(min(2, max(1, len(imgs))))
            for i, img in enumerate(imgs):
                cols[i % len(cols)].image(str(img), caption=img.name)

with tabs[3]:
    if not payload:
        st.info("Run a comparison first.")
    else:
        q = st.text_input("Question", "What changed near 26-PIT-9062?")
        if st.button("Ask"):
            ans = chat_on_run(payload, q)
            st.write(ans["answer"])
            st.caption(f"provider={ans.get('provider')} confidence={ans.get('confidence')} unsupported={ans.get('unsupported')}")
            for c in ans.get("citations") or []:
                with st.expander(c.get("source_id")):
                    st.json(c)

with tabs[4]:
    if not payload:
        st.info("Run a comparison first.")
    else:
        run_dir = Path(payload["run_dir"])
        for name in ["trace.json", "metrics.json", "events.jsonl", "llm_calls.jsonl"]:
            p = run_dir / name
            st.subheader(name)
            if p.exists():
                if name.endswith(".json"):
                    st.json(json.loads(p.read_text(encoding="utf-8")))
                else:
                    st.code(p.read_text(encoding="utf-8")[-4000:])
            else:
                st.write("missing")

with tabs[5]:
    st.write("Run `make eval` or `uv run delta-chat eval` to refresh the scorecard.")
    eval_root = project_root() / "artifacts" / "eval"
    if eval_root.exists():
        runs = sorted([p for p in eval_root.iterdir() if p.is_dir()], reverse=True)
        if runs:
            latest = runs[0]
            sc = latest / "scorecard.json"
            st.write(f"Latest: `{latest.name}`")
            if sc.exists():
                st.json(json.loads(sc.read_text(encoding="utf-8")).get("summary"))
            md = latest / "scorecard.md"
            if md.exists():
                st.markdown(md.read_text(encoding="utf-8"))
    else:
        st.info("No eval artifacts yet.")
