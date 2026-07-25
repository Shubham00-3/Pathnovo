import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type ChatAnswer,
  type DeltaChange,
  type PidInfo,
  type RunSummary,
} from "./api";

type Tab = "setup" | "delta" | "markup" | "chat" | "obs" | "eval";
type ChatTurn = { question: string; answer: ChatAnswer };
type EvalData = {
  available: boolean;
  // "run" is a scorecard produced on this instance; "baseline" is the committed
  // record of a previous verified run. They must not look the same.
  source?: "run" | "baseline" | null;
  run_id?: string;
  scorecard?: { summary?: Record<string, unknown> };
  scorecard_md?: string;
};

const CHANGE_TYPES = ["added", "removed", "modified", "moved", "moved_modified"] as const;
const BANDS = ["high", "medium", "low"] as const;
const PAGE_SIZE = 25;
const TABS: Array<[Tab, string]> = [
  ["setup", "Pair setup"],
  ["delta", "Delta"],
  ["markup", "Markup"],
  ["chat", "Chat"],
  ["obs", "Observability"],
  ["eval", "Evaluation"],
];

function entries(value: unknown): Array<[string, string]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).map(([key, item]) => [
    key,
    String(item),
  ]);
}

function Pills({ value }: { value: unknown }) {
  const items = entries(value);
  return (
    <div className="summary-pills">
      {items.length === 0 && <span className="muted">None</span>}
      {items.map(([label, count]) => (
        <span className="mini-pill" key={label}>
          {label}: {count}
        </span>
      ))}
    </div>
  );
}

const FORMAT_LABEL: Record<string, string> = {
  "application/pdf": "PDF",
  "image/vnd.dxf": "DXF (CAD)",
  "image/vnd.dwg": "DWG (CAD)",
};

function formatBytes(bytes?: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * What the resolver actually resolved, shown before anything runs.
 *
 * The setup tab is the first thing a user sees and had nothing on it but three
 * dropdowns. This fills it with the one fact that decides whether the
 * comparison is even meaningful: whether both PIDs belong to the same
 * underlying document. That is the pair-compatibility precondition, and
 * surfacing it here means a mismatch is visible before a pipeline run rather
 * than after one.
 */
function ResolvedPair({ a, b }: { a?: PidInfo; b?: PidInfo }) {
  if (!a || !b) return null;
  const sameDoc =
    !!a.underlying_document_id && a.underlying_document_id === b.underlying_document_id;

  return (
    <div className="resolved">
      <div className="resolved-grid">
        {[a, b].map((doc, index) => (
          <div className="resolved-col" key={doc.pid}>
            <div className="label">{index === 0 ? "Base" : "Revised"}</div>
            <div className="resolved-pid mono">{doc.pid}</div>
            <dl className="resolved-meta">
              <dt>Name</dt>
              <dd>{doc.display_name || "—"}</dd>
              <dt>Revision</dt>
              <dd>{doc.revision_label || "—"}</dd>
              <dt>Format</dt>
              <dd>{FORMAT_LABEL[doc.media_type || ""] || doc.media_type || "—"}</dd>
              <dt>Size</dt>
              <dd>{formatBytes(doc.byte_size)}</dd>
              <dt>Document</dt>
              <dd className="mono">{doc.underlying_document_id || "—"}</dd>
            </dl>
          </div>
        ))}
      </div>
      <p className={`resolved-verdict ${sameDoc ? "ok" : "warn"}`}>
        {sameDoc
          ? "Both PIDs resolve to the same underlying document — a plausible revision pair."
          : "These PIDs belong to different underlying documents. Expect a pair-compatibility warning; strict mode will refuse."}
      </p>
    </div>
  );
}

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
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [question, setQuestion] = useState("What changed near 26-PIT-9062?");
  const [chatLog, setChatLog] = useState<ChatTurn[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [trace, setTrace] = useState("");
  const [metrics, setMetrics] = useState("");
  const [events, setEvents] = useState("");
  const [evalData, setEvalData] = useState<EvalData | null>(null);
  const [apiOk, setApiOk] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .health()
      .then(() => setApiOk(true))
      .catch(() => setApiOk(false));
    api
      .listPids()
      .then(({ pids: available }) => {
        setPids(available);
        const ids = available.map((pid) => pid.pid);
        setPidA(ids.includes("PID-SYN-A") ? "PID-SYN-A" : ids[0] || "");
        setPidB(ids.includes("PID-SYN-B") ? "PID-SYN-B" : ids[1] || ids[0] || "");
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : String(reason)),
      );
  }, []);

  const loadObservability = useCallback(async (runId: string) => {
    const fetchText = async (relative: string) => {
      try {
        const response = await fetch(api.runFile(runId, relative));
        return response.ok ? await response.text() : `(missing ${relative})`;
      } catch (reason) {
        return String(reason);
      }
    };
    const [traceText, metricsText, eventsText] = await Promise.all([
      fetchText("trace.json"),
      fetchText("metrics.json"),
      fetchText("events.jsonl"),
    ]);
    setTrace(traceText);
    setMetrics(metricsText);
    setEvents(eventsText.slice(-6000));
  }, []);

  const runComparison = async () => {
    setLoading(true);
    setError(null);
    setOkMsg(null);
    setRun(null);
    setChatLog([]);
    setTrace("");
    setMetrics("");
    setEvents("");
    setPage(1);
    try {
      const completed = await api.runPair({
        pid_a: pidA,
        pid_b: pidB,
        mismatch_mode: mode,
      });
      setRun(completed);
      setOkMsg(`Comparison complete · request_id=${completed.request_id}`);
      setTab("delta");
      void loadObservability(completed.request_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const ask = async () => {
    if (!run?.request_id || !question.trim()) return;
    setChatLoading(true);
    setError(null);
    try {
      const response = await api.chat(run.request_id, question.trim());
      setChatLog((previous) => [
        ...previous,
        { question: response.question, answer: response.answer },
      ]);
      void loadObservability(run.request_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setChatLoading(false);
    }
  };

  const loadEval = useCallback(async () => {
    try {
      setEvalData(await api.latestEval());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  const changes: DeltaChange[] = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (run?.delta?.changes || []).filter((change) => {
      if (!types.includes(change.change_type) || !bands.includes(change.confidence_band)) {
        return false;
      }
      if (!needle) return true;
      return [
        change.delta_item_id,
        change.change_type,
        change.entity_type,
        change.deterministic_description,
        change.before,
        change.after,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [run, types, bands, search]);

  const pageCount = Math.max(1, Math.ceil(changes.length / PAGE_SIZE));
  const pagedChanges = changes.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const selectedA = pids.find((p) => p.pid === pidA);
  const selectedB = pids.find((p) => p.pid === pidB);
  const summary = run?.delta?.summary || run?.summary || {};
  const compatibility = run?.delta?.pair_compatibility || run?.pair_compatibility || {};
  const warnings = run?.delta?.warnings || run?.warnings || [];
  const evalSummary = (evalData?.scorecard?.summary || {}) as Record<string, unknown>;
  const evalGates =
    evalSummary.gates && typeof evalSummary.gates === "object"
      ? (evalSummary.gates as Record<string, unknown>)
      : {};

  const toggle = (list: string[], value: string, setter: (items: string[]) => void) => {
    setPage(1);
    setter(list.includes(value) ? list.filter((item) => item !== value) : [...list, value]);
  };

  const showTab = (nextTab: Tab) => {
    setTab(nextTab);
    if (nextTab === "eval") void loadEval();
    if (nextTab === "obs" && run?.request_id) {
      void loadObservability(run.request_id);
    }
  };

  const showCitation = (sourceId: string) => {
    if (sourceId.startsWith("D:")) {
      setSearch(sourceId.slice(2));
      setPage(1);
      showTab("delta");
    } else {
      showTab("markup");
    }
  };

  return (
    <div className="app">
      <header className="hero">
        <h1>Document Delta &amp; Grounded Chat</h1>
        <p className="sub">
          Compare two P&amp;ID revisions, inspect structured and visual changes, follow the
          processing trace, and ask questions backed by retrieved evidence.
        </p>
        <div className="meta">
          <span className={`status ${apiOk ? "up" : "down"}`}>
            <i aria-hidden="true" />
            {apiOk === null ? "connecting" : apiOk ? "API connected" : "API offline"}
          </span>
          {run && (
            <span className="meta-item">
              run <b className="mono">{run.request_id}</b>
            </span>
          )}
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="Submission sections">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            className={`tab ${tab === id ? "active" : ""}`}
            type="button"
            role="tab"
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            onClick={() => showTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {error && <div className="alert error" role="alert">{error}</div>}
      {okMsg && <div className="alert ok" role="status" aria-live="polite">{okMsg}</div>}

      {tab === "setup" && (
        <section className="panel" id="panel-setup" role="tabpanel">
          <div className="grid2">
            <div className="field">
              <label htmlFor="pid-a">PID A (base)</label>
              <select id="pid-a" value={pidA} onChange={(event) => setPidA(event.target.value)}>
                {pids.map((pid) => (
                  <option key={pid.pid} value={pid.pid}>
                    {pid.pid}{pid.revision_label ? ` · rev ${pid.revision_label}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="pid-b">PID B (revised)</label>
              <select id="pid-b" value={pidB} onChange={(event) => setPidB(event.target.value)}>
                {pids.map((pid) => (
                  <option key={pid.pid} value={pid.pid}>
                    {pid.pid}{pid.revision_label ? ` · rev ${pid.revision_label}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="mismatch-mode">Mismatch mode</label>
              <select
                id="mismatch-mode"
                value={mode}
                onChange={(event) => setMode(event.target.value)}
              >
                <option value="warn">warn</option>
                <option value="strict">strict</option>
                <option value="force">force</option>
              </select>
            </div>
          </div>
          <div className="row">
            <button
              className="btn primary"
              type="button"
              disabled={loading || !apiOk || !pidA || !pidB}
              onClick={() => void runComparison()}
            >
              {loading ? <><span className="spinner" aria-hidden="true" />Running pipeline…</> : "Run comparison"}
            </button>
            <span className="muted">
              Start with <span className="mono">PID-SYN-A / PID-SYN-B</span>.
            </span>
          </div>

          <ResolvedPair a={selectedA} b={selectedB} />
          {run && (
            <div className="cards">
              <div className="card"><div className="label">Changes</div><div className="value">{String(summary.total_changes ?? "—")}</div></div>
              <div className="card"><div className="label">Compatible</div><div className="value small">{String(compatibility.compatible ?? "—")}</div></div>
              <div className="card"><div className="label">Score</div><div className="value">{String(compatibility.score ?? "—")}</div></div>
              <div className="card"><div className="label">Request</div><div className="value small mono">{run.request_id}</div></div>
            </div>
          )}
        </section>
      )}

      {tab === "delta" && (
        <section className="panel" id="panel-delta" role="tabpanel">
          {!run ? <p className="muted">Run a comparison first.</p> : (
            <>
              {warnings.length > 0 && <div className="alert warn">{warnings.join("\n")}</div>}
              <div className="cards">
                <div className="card"><div className="label">Total</div><div className="value">{String(summary.total_changes ?? 0)}</div></div>
                <div className="card"><div className="label">By type</div><Pills value={summary.by_change_type} /></div>
                <div className="card"><div className="label">Confidence</div><Pills value={summary.by_confidence_band} /></div>
                <div className="card"><div className="label">Cross-document</div><div className="value small">{String(summary.cross_document ?? false)}</div></div>
              </div>
              <div className="filters" aria-label="Delta filters">
                {CHANGE_TYPES.map((type) => (
                  <label key={type}><input type="checkbox" checked={types.includes(type)} onChange={() => toggle(types, type, setTypes)} />{type}</label>
                ))}
                {BANDS.map((band) => (
                  <label key={band}><input type="checkbox" checked={bands.includes(band)} onChange={() => toggle(bands, band, setBands)} />{band}</label>
                ))}
              </div>
              <div className="field search-field">
                <label htmlFor="delta-search">Search delta rows</label>
                <input
                  id="delta-search"
                  type="search"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    setPage(1);
                  }}
                  placeholder="ID, tag, value, or description…"
                />
              </div>
              <div className="row artifact-links">
                {run.paths?.report_md && <a className="btn" href={run.paths.report_md} target="_blank" rel="noreferrer">report.md</a>}
                {run.paths?.delta_json && <a className="btn" href={run.paths.delta_json} target="_blank" rel="noreferrer">delta.json</a>}
                {run.paths?.report_html && <a className="btn" href={run.paths.report_html} target="_blank" rel="noreferrer">report.html</a>}
              </div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>ID</th><th>Type</th><th>Entity</th><th>Band</th><th>Description</th><th>Before</th><th>After</th></tr></thead>
                  <tbody>
                    {pagedChanges.map((change) => (
                      <tr key={change.delta_item_id} id={`change-${change.delta_item_id}`}>
                        <td className="mono">{change.delta_item_id}</td>
                        <td><span className={`badge ${change.change_type}`}>{change.change_type}</span></td>
                        <td>{change.entity_type}</td>
                        <td><span className={`badge ${change.confidence_band}`}>{change.confidence_band}</span></td>
                        <td>{change.deterministic_description}</td>
                        <td className="mono">{change.before || "—"}</td>
                        <td className="mono">{change.after || "—"}</td>
                      </tr>
                    ))}
                    {changes.length === 0 && <tr><td colSpan={7} className="muted">No changes match the current filters.</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="row pagination" aria-label="Delta pagination">
                <button className="btn" type="button" disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button>
                <span className="muted">Page {page} of {pageCount} · {changes.length} matching changes</span>
                <button className="btn" type="button" disabled={page >= pageCount} onClick={() => setPage((current) => Math.min(pageCount, current + 1))}>Next</button>
              </div>
            </>
          )}
        </section>
      )}

      {tab === "markup" && (
        <section className="panel" id="panel-markup" role="tabpanel">
          {!run ? <p className="muted">Run a comparison first.</p> : (
            <>
              <div className="row">
                {run.paths?.markup_pdf && <a className="btn primary" href={run.paths.markup_pdf} target="_blank" rel="noreferrer">Download markup.pdf</a>}
                <span className="muted">Green = added · Red = removed · Amber = modified/moved · Gray = low confidence</span>
              </div>
              <div className="gallery">
                {(run.markup_previews?.length ? run.markup_previews : run.renders || []).map((src, index) => (
                  <figure key={src}>
                    <a href={src} target="_blank" rel="noreferrer"><img src={src} alt={`Annotated markup preview page ${index + 1}`} /></a>
                    <figcaption className="mono">{src.split("/").pop()}</figcaption>
                  </figure>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {tab === "chat" && (
        <section className="panel" id="panel-chat" role="tabpanel">
          {!run ? <p className="muted">Run a comparison first.</p> : (
            <>
              <div className="field">
                <label htmlFor="chat-question">Question</label>
                <input id="chat-question" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void ask(); }} placeholder="What changed near 26-PIT-9062?" />
              </div>
              <div className="row">
                <button className="btn primary" type="button" disabled={chatLoading} onClick={() => void ask()}>
                  {chatLoading ? <><span className="spinner" aria-hidden="true" />Asking…</> : "Ask"}
                </button>
                <button className="btn" type="button" onClick={() => setQuestion("Summarize only high-confidence changes.")}>High-confidence summary</button>
                <button className="btn" type="button" onClick={() => setQuestion("Did the motor vendor change?")}>Unsupported example</button>
              </div>
              <div className="chat-log" aria-live="polite">
                {chatLog.map((turn, index) => (
                  <div key={`${turn.question}-${index}`}>
                    <div className="bubble q"><div className="muted bubble-label">Question</div>{turn.question}</div>
                    <div className="bubble a">
                      <div className="muted bubble-label">Answer · {turn.answer.provider || "—"} · {turn.answer.confidence}{turn.answer.unsupported ? " · unsupported" : ""}</div>
                      <div>{turn.answer.answer}</div>
                      {turn.answer.citations.map((citation) => (
                        <details key={citation.source_id} className="cite">
                          <summary>{citation.source_id}</summary>
                          <div className="mono muted">{citation.quote || "(no quote)"}{citation.page != null ? ` · page ${citation.page}` : ""}{citation.grid_region ? ` · grid ${citation.grid_region}` : ""}</div>
                          <button className="evidence-link" type="button" onClick={() => showCitation(citation.source_id)}>View evidence</button>
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
        <section className="panel" id="panel-obs" role="tabpanel">
          {!run ? <p className="muted">Run a comparison first.</p> : (
            <>
              <div className="row artifact-links">
                {run.paths?.trace && <a className="btn" href={run.paths.trace} target="_blank" rel="noreferrer">trace.json</a>}
                {run.paths?.metrics && <a className="btn" href={run.paths.metrics} target="_blank" rel="noreferrer">metrics.json</a>}
                {run.paths?.events && <a className="btn" href={run.paths.events} target="_blank" rel="noreferrer">events.jsonl</a>}
                {run.paths?.llm_calls && <a className="btn" href={run.paths.llm_calls} target="_blank" rel="noreferrer">llm_calls.jsonl</a>}
              </div>
              <h3>Trace</h3><pre className="pre">{trace || "—"}</pre>
              <h3>Metrics</h3><pre className="pre">{metrics || "—"}</pre>
              <h3>Events (tail)</h3><pre className="pre">{events || "—"}</pre>
            </>
          )}
        </section>
      )}

      {tab === "eval" && (
        <section className="panel" id="panel-eval" role="tabpanel">
          <div className="row">
            <button className="btn" type="button" onClick={() => void loadEval()}>Refresh scorecard</button>
            <span className="muted">Run <span className="mono">python -m eval.run</span> to regenerate.</span>
          </div>
          {!evalData?.available ? <p className="muted">No evaluation artifacts yet.</p> : (
            <>
              {evalData.source === "baseline" ? (
                <p className="muted">
                  Committed baseline <span className="mono">{evalData.run_id}</span> — no eval has
                  run on this instance. Run <span className="mono">python -m eval.run</span> for a
                  live scorecard.
                </p>
              ) : (
                <p className="muted">Latest run: <span className="mono">{evalData.run_id}</span></p>
              )}
              <div className="cards">
                <div className="card"><div className="label">All gates</div><div className="value small">{evalSummary.all_gates_passed === true ? "PASS" : "FAIL"}</div></div>
                <div className="card"><div className="label">Native F1</div><div className="value">{String(evalSummary.native_delta_f1 ?? "—")}</div></div>
                <div className="card"><div className="label">Scanned F1</div><div className="value">{String(evalSummary.scanned_delta_f1 ?? "—")}</div></div>
                <div className="card"><div className="label">Chat facts</div><div className="value">{String(evalSummary.chat_fact_accuracy ?? "—")}</div></div>
              </div>
              <div className="summary-pills" aria-label="Evaluation gates">
                {Object.entries(evalGates).map(([name, passed]) => (
                  <span className={`mini-pill ${passed === true ? "pass" : "fail"}`} key={name}>{passed === true ? "PASS" : "FAIL"} · {name}</span>
                ))}
              </div>
              {Array.isArray(evalSummary.required_failures) && evalSummary.required_failures.length > 0 && (
                <div className="alert error" role="alert">Required failures: {evalSummary.required_failures.join("; ")}</div>
              )}
              <details className="scorecard-details">
                <summary>Raw scorecard details</summary>
                <pre className="pre">{JSON.stringify(evalSummary, null, 2)}</pre>
              </details>
            </>
          )}
        </section>
      )}

      <footer className="footer">
        Deterministic delta engine · grounded answers with validated citations
      </footer>
    </div>
  );
}
