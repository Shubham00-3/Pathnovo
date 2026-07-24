import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type ChatAnswer,
  type DeltaChange,
  type PidInfo,
  type RunSummary,
} from "./api";

type Tab = "setup" | "delta" | "markup" | "chat" | "obs" | "eval";

const CHANGE_TYPES = ["added", "removed", "modified", "moved", "moved_modified"] as const;
const BANDS = ["high", "medium", "low"] as const;

type ChatTurn = { question: string; answer: ChatAnswer };

export default function App() {
  const [tab, setTab] = useState<Tab>("setup");
  const [pids, setPids] = useState<PidInfo[]>([]);
  const [pidA, setPidA] = useState("PID-SYN-A");
  const [pidB, setPidB] = useState("PID-SYN-B");
  const [mode, setMode] = useState("warn");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [types, setTypes] = useState<string[]>([...CHANGE_TYPES]);
  const [bands, setBands] = useState<string[]>([...BANDS]);
  const [question, setQuestion] = useState("What changed near 26-PIT-9062?");
  const [chatLog, setChatLog] = useState<ChatTurn[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [trace, setTrace] = useState<string>("");
  const [metrics, setMetrics] = useState<string>("");
  const [events, setEvents] = useState<string>("");
  const [evalData, setEvalData] = useState<{
    available: boolean;
    run_id?: string;
    scorecard?: { summary?: Record<string, unknown> };
    scorecard_md?: string;
  } | null>(null);
  const [apiOk, setApiOk] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .health()
      .then(() => setApiOk(true))
      .catch(() => setApiOk(false));
    api
      .listPids()
      .then((r) => {
        setPids(r.pids);
        const ids = r.pids.map((p) => p.pid);
        if (ids.includes("PID-SYN-A")) setPidA("PID-SYN-A");
        else if (ids[0]) setPidA(ids[0]);
        if (ids.includes("PID-SYN-B")) setPidB("PID-SYN-B");
        else if (ids[1]) setPidB(ids[1]);
        else if (ids[0]) setPidB(ids[0]);
      })
      .catch((e) => setError(String(e.message || e)));
  }, []);

  const loadObservability = useCallback(async (runId: string) => {
    const fetchText = async (rel: string) => {
      try {
        const res = await fetch(api.runFile(runId, rel));
        if (!res.ok) return `(missing ${rel})`;
        return await res.text();
      } catch (e) {
        return String(e);
      }
    };
    const [t, m, e] = await Promise.all([
      fetchText("trace.json"),
      fetchText("metrics.json"),
      fetchText("events.jsonl"),
    ]);
    setTrace(t);
    setMetrics(m);
    setEvents(e.slice(-6000));
  }, []);

  const runComparison = async () => {
    setLoading(true);
    setError(null);
    setOkMsg(null);
    setChatLog([]);
    try {
      const result = await api.runPair({
        pid_a: pidA,
        pid_b: pidB,
        mismatch_mode: mode,
      });
      const full = await api.getRun(result.request_id);
      setRun(full);
      setOkMsg(`Comparison complete · request_id=${full.request_id}`);
      setTab("delta");
      void loadObservability(full.request_id);
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setLoading(false);
    }
  };

  const ask = async () => {
    if (!run?.request_id || !question.trim()) return;
    setChatLoading(true);
    setError(null);
    try {
      const res = await api.chat(run.request_id, question.trim());
      setChatLog((prev) => [...prev, { question: res.question, answer: res.answer }]);
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setChatLoading(false);
    }
  };

  const loadEval = async () => {
    try {
      const data = await api.latestEval();
      setEvalData(data);
    } catch (e) {
      setError(String((e as Error).message || e));
    }
  };

  useEffect(() => {
    if (tab === "eval") void loadEval();
    if (tab === "obs" && run?.request_id) void loadObservability(run.request_id);
  }, [tab, run?.request_id, loadObservability]);

  const changes: DeltaChange[] = useMemo(() => {
    const all = run?.delta?.changes || [];
    return all.filter(
      (c) => types.includes(c.change_type) && bands.includes(c.confidence_band)
    );
  }, [run, types, bands]);

  const summary = run?.delta?.summary || run?.summary || {};
  const compat = run?.delta?.pair_compatibility || run?.pair_compatibility || {};
  const warnings = run?.delta?.warnings || run?.warnings || [];

  const toggle = (list: string[], value: string, setter: (v: string[]) => void) => {
    setter(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  };

  return (
    <div className="app">
      <header className="hero">
        <span className="kicker">Applied AI · Document Delta</span>
        <h1>
          Document Delta & <span>Grounded Chat</span>
        </h1>
        <p className="sub">
          React frontend over a deterministic, coordinate-aware delta engine. Compare two PID
          revisions, inspect structured changes, markup, traces, and ask cited questions.
        </p>
        <div className="meta">
          <div className="chip">
            UI · <b>React + Vite</b>
          </div>
          <div className="chip">
            API · <b>FastAPI</b>
          </div>
          <div className="chip">
            Backend · <b>{apiOk === null ? "…" : apiOk ? "connected" : "offline"}</b>
          </div>
          {run?.request_id && (
            <div className="chip">
              Run · <b className="mono">{run.request_id}</b>
            </div>
          )}
        </div>
      </header>

      <nav className="tabs">
        {(
          [
            ["setup", "Pair setup"],
            ["delta", "Delta"],
            ["markup", "Markup"],
            ["chat", "Chat"],
            ["obs", "Observability"],
            ["eval", "Evaluation"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={`tab ${tab === id ? "active" : ""}`}
            onClick={() => setTab(id)}
            type="button"
          >
            {label}
          </button>
        ))}
      </nav>

      {error && <div className="alert error">{error}</div>}
      {okMsg && <div className="alert ok">{okMsg}</div>}

      {tab === "setup" && (
        <section className="panel">
          <div className="grid2">
            <div className="field">
              <label>PID A (base)</label>
              <select value={pidA} onChange={(e) => setPidA(e.target.value)}>
                {pids.map((p) => (
                  <option key={p.pid} value={p.pid}>
                    {p.pid}
                    {p.revision_label ? ` · rev ${p.revision_label}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>PID B (revised)</label>
              <select value={pidB} onChange={(e) => setPidB(e.target.value)}>
                {pids.map((p) => (
                  <option key={p.pid} value={p.pid}>
                    {p.pid}
                    {p.revision_label ? ` · rev ${p.revision_label}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Mismatch mode</label>
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="warn">warn</option>
                <option value="strict">strict</option>
                <option value="force">force</option>
              </select>
            </div>
          </div>
          <div className="row">
            <button className="btn primary" disabled={loading || !apiOk} onClick={runComparison}>
              {loading ? (
                <>
                  <span className="spinner" /> Running pipeline…
                </>
              ) : (
                "Run comparison"
              )}
            </button>
            <span className="muted">
              Try <span className="mono">PID-SYN-A / PID-SYN-B</span> or{" "}
              <span className="mono">PID-LIFT / PID-EXPORT</span> (mismatch).
            </span>
          </div>
          {run && (
            <div className="cards" style={{ marginTop: 16 }}>
              <div className="card">
                <div className="label">Changes</div>
                <div className="value">{String((summary as { total_changes?: number }).total_changes ?? "—")}</div>
              </div>
              <div className="card">
                <div className="label">Compatible</div>
                <div className="value" style={{ fontSize: 18 }}>
                  {String((compat as { compatible?: boolean }).compatible ?? "—")}
                </div>
              </div>
              <div className="card">
                <div className="label">Score</div>
                <div className="value">{String((compat as { score?: number }).score ?? "—")}</div>
              </div>
              <div className="card">
                <div className="label">Request</div>
                <div className="value mono" style={{ fontSize: 14 }}>
                  {run.request_id}
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "delta" && (
        <section className="panel">
          {!run ? (
            <p className="muted">Run a comparison first.</p>
          ) : (
            <>
              {warnings.length > 0 && (
                <div className="alert warn">{warnings.join("\n")}</div>
              )}
              <div className="cards">
                <div className="card">
                  <div className="label">Total</div>
                  <div className="value">
                    {String((summary as { total_changes?: number }).total_changes ?? 0)}
                  </div>
                </div>
                <div className="card">
                  <div className="label">By type</div>
                  <div className="value mono" style={{ fontSize: 12, fontWeight: 500 }}>
                    {JSON.stringify((summary as { by_change_type?: object }).by_change_type || {})}
                  </div>
                </div>
                <div className="card">
                  <div className="label">Confidence</div>
                  <div className="value mono" style={{ fontSize: 12, fontWeight: 500 }}>
                    {JSON.stringify(
                      (summary as { by_confidence_band?: object }).by_confidence_band || {}
                    )}
                  </div>
                </div>
                <div className="card">
                  <div className="label">Cross-doc</div>
                  <div className="value" style={{ fontSize: 18 }}>
                    {String((summary as { cross_document?: boolean }).cross_document ?? false)}
                  </div>
                </div>
              </div>

              <div className="filters">
                {CHANGE_TYPES.map((t) => (
                  <label key={t}>
                    <input
                      type="checkbox"
                      checked={types.includes(t)}
                      onChange={() => toggle(types, t, setTypes)}
                    />
                    {t}
                  </label>
                ))}
                {BANDS.map((b) => (
                  <label key={b}>
                    <input
                      type="checkbox"
                      checked={bands.includes(b)}
                      onChange={() => toggle(bands, b, setBands)}
                    />
                    {b}
                  </label>
                ))}
              </div>

              <div className="row" style={{ marginBottom: 12 }}>
                {run.paths?.report_md && (
                  <a className="btn" href={run.paths.report_md} target="_blank" rel="noreferrer">
                    report.md
                  </a>
                )}
                {run.paths?.delta_json && (
                  <a className="btn" href={run.paths.delta_json} target="_blank" rel="noreferrer">
                    delta.json
                  </a>
                )}
                {run.paths?.report_html && (
                  <a className="btn" href={run.paths.report_html} target="_blank" rel="noreferrer">
                    report.html
                  </a>
                )}
              </div>

              <div style={{ overflowX: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Type</th>
                      <th>Entity</th>
                      <th>Band</th>
                      <th>Description</th>
                      <th>Before</th>
                      <th>After</th>
                    </tr>
                  </thead>
                  <tbody>
                    {changes.map((c) => (
                      <tr key={c.delta_item_id}>
                        <td className="mono">{c.delta_item_id}</td>
                        <td>
                          <span className={`badge ${c.change_type}`}>{c.change_type}</span>
                        </td>
                        <td>{c.entity_type}</td>
                        <td>
                          <span className={`badge ${c.confidence_band}`}>{c.confidence_band}</span>
                        </td>
                        <td>{c.deterministic_description}</td>
                        <td className="mono">{c.before || "—"}</td>
                        <td className="mono">{c.after || "—"}</td>
                      </tr>
                    ))}
                    {changes.length === 0 && (
                      <tr>
                        <td colSpan={7} className="muted">
                          No changes match filters.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}

      {tab === "markup" && (
        <section className="panel">
          {!run ? (
            <p className="muted">Run a comparison first.</p>
          ) : (
            <>
              <div className="row">
                {run.paths?.markup_pdf && (
                  <a className="btn primary" href={run.paths.markup_pdf} target="_blank" rel="noreferrer">
                    Download markup.pdf
                  </a>
                )}
                <span className="muted">
                  Green = added · Red = removed · Amber = modified/moved · Gray = low confidence
                </span>
              </div>
              <div className="gallery" style={{ marginTop: 16 }}>
                {(run.renders || []).map((src) => (
                  <figure key={src}>
                    <img src={src} alt={src} />
                    <figcaption className="mono">{src.split("/").pop()}</figcaption>
                  </figure>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {tab === "chat" && (
        <section className="panel">
          {!run ? (
            <p className="muted">Run a comparison first.</p>
          ) : (
            <>
              <div className="field">
                <label>Question</label>
                <input
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void ask();
                  }}
                  placeholder="What changed near 26-PIT-9062?"
                />
              </div>
              <div className="row">
                <button className="btn primary" disabled={chatLoading} onClick={ask}>
                  {chatLoading ? (
                    <>
                      <span className="spinner" /> Asking…
                    </>
                  ) : (
                    "Ask"
                  )}
                </button>
                <button
                  className="btn"
                  type="button"
                  onClick={() => setQuestion("Summarize only high-confidence changes.")}
                >
                  High-conf summary
                </button>
                <button
                  className="btn"
                  type="button"
                  onClick={() => setQuestion("Did the motor vendor change?")}
                >
                  Unsupported example
                </button>
              </div>
              <div className="chat-log">
                {chatLog.map((t, i) => (
                  <div key={i}>
                    <div className="bubble q">
                      <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                        Question
                      </div>
                      {t.question}
                    </div>
                    <div className="bubble a" style={{ marginTop: 8 }}>
                      <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                        Answer · {t.answer.provider || "—"} · {t.answer.confidence}
                        {t.answer.unsupported ? " · unsupported" : ""}
                      </div>
                      <div>{t.answer.answer}</div>
                      {(t.answer.citations || []).map((c) => (
                        <details key={c.source_id} className="cite">
                          <summary>{c.source_id}</summary>
                          <div className="mono muted" style={{ marginTop: 6 }}>
                            {c.quote || "(no quote)"}
                            {c.page != null ? ` · page ${c.page}` : ""}
                            {c.grid_region ? ` · grid ${c.grid_region}` : ""}
                          </div>
                        </details>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {tab === "obs" && (
        <section className="panel">
          {!run ? (
            <p className="muted">Run a comparison first.</p>
          ) : (
            <>
              <div className="row" style={{ marginBottom: 12 }}>
                {run.paths?.trace && (
                  <a className="btn" href={run.paths.trace} target="_blank" rel="noreferrer">
                    trace.json
                  </a>
                )}
                {run.paths?.metrics && (
                  <a className="btn" href={run.paths.metrics} target="_blank" rel="noreferrer">
                    metrics.json
                  </a>
                )}
                {run.paths?.events && (
                  <a className="btn" href={run.paths.events} target="_blank" rel="noreferrer">
                    events.jsonl
                  </a>
                )}
                {run.paths?.llm_calls && (
                  <a className="btn" href={run.paths.llm_calls} target="_blank" rel="noreferrer">
                    llm_calls.jsonl
                  </a>
                )}
              </div>
              <h3>Trace</h3>
              <pre className="pre">{trace || "—"}</pre>
              <h3>Metrics</h3>
              <pre className="pre">{metrics || "—"}</pre>
              <h3>Events (tail)</h3>
              <pre className="pre">{events || "—"}</pre>
            </>
          )}
        </section>
      )}

      {tab === "eval" && (
        <section className="panel">
          <div className="row">
            <button className="btn" onClick={loadEval}>
              Refresh scorecard
            </button>
            <span className="muted">
              Run <span className="mono">python -m eval.run</span> to regenerate.
            </span>
          </div>
          {!evalData?.available ? (
            <p className="muted" style={{ marginTop: 12 }}>
              No eval artifacts yet.
            </p>
          ) : (
            <>
              <p className="muted">
                Latest run: <span className="mono">{evalData.run_id}</span>
              </p>
              <pre className="pre">
                {JSON.stringify(evalData.scorecard?.summary || evalData.scorecard, null, 2)}
              </pre>
              {evalData.scorecard_md && (
                <>
                  <h3>scorecard.md</h3>
                  <pre className="pre">{evalData.scorecard_md}</pre>
                </>
              )}
            </>
          )}
        </section>
      )}

      <footer className="footer">
        Business logic lives in the Python pipeline. This React app is a thin client over{" "}
        <span className="mono">/api/*</span>.
      </footer>
    </div>
  );
}
