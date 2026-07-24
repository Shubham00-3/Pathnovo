const API_BASE = import.meta.env.VITE_API_BASE ?? "";

function formatApiError(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (typeof d.message === "string") return d.message;
    if (typeof d.detail === "string") return d.detail;
    if (d.detail && typeof d.detail === "object") {
      const inner = d.detail as Record<string, unknown>;
      if (typeof inner.message === "string") {
        const reasons = Array.isArray(inner.details)
          ? ""
          : inner.details && typeof inner.details === "object"
            ? ` (${JSON.stringify((inner.details as { reasons?: string[] }).reasons || inner.details)})`
            : "";
        return `${inner.message}${reasons}`;
      }
    }
    if (Array.isArray(d.detail)) {
      return d.detail
        .map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x)))
        .join("; ");
    }
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return fallback;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180_000);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      signal: controller.signal,
      ...init,
    });
    if (!res.ok) {
      let detail: unknown = res.statusText;
      try {
        detail = await res.json();
      } catch {
        /* ignore */
      }
      throw new Error(formatApiError(detail, res.statusText));
    }
    return res.json() as Promise<T>;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error("Request timed out (pipeline may still be running on dense/OCR jobs).");
    }
    throw e;
  } finally {
    clearTimeout(timeout);
  }
}

export type PidInfo = {
  pid: string;
  display_name?: string;
  revision_label?: string;
  underlying_document_id?: string;
  media_type?: string;
  byte_size?: number;
  error?: string;
};

export type DeltaChange = {
  delta_item_id: string;
  change_type: string;
  entity_type: string;
  page_a?: number | null;
  page_b?: number | null;
  region?: Record<string, unknown>;
  before?: string | null;
  after?: string | null;
  deterministic_description: string;
  confidence: number;
  confidence_band: string;
  review_required?: boolean;
};

export type DeltaReport = {
  delta_id: string;
  pid_a: string;
  pid_b: string;
  summary?: Record<string, unknown>;
  pair_compatibility?: Record<string, unknown>;
  warnings?: string[];
  changes: DeltaChange[];
  metrics?: Record<string, unknown>;
};

export type RunSummary = {
  request_id: string;
  run_dir?: string;
  pid_a?: string;
  pid_b?: string;
  delta?: DeltaReport;
  summary?: Record<string, unknown>;
  pair_compatibility?: Record<string, unknown>;
  warnings?: string[];
  paths?: Record<string, string>;
  renders?: string[];
};

export type ChatAnswer = {
  answer: string;
  citations: Array<{
    source_id: string;
    source_family?: string;
    pid?: string;
    page?: number;
    grid_region?: string;
    quote?: string;
    bbox?: number[];
  }>;
  confidence: string;
  unsupported: boolean;
  route?: string;
  provider?: string;
};

export function fileUrl(path: string): string {
  if (path.startsWith("http") || path.startsWith("/api/")) return `${API_BASE}${path.startsWith("/api/") ? path : path}`;
  return `${API_BASE}${path}`;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  listPids: () => request<{ pids: PidInfo[] }>("/api/pids"),
  runPair: (body: { pid_a: string; pid_b: string; mismatch_mode: string }) =>
    request<RunSummary>("/api/run-pair", { method: "POST", body: JSON.stringify(body) }),
  listRuns: () => request<{ runs: RunSummary[] }>("/api/runs"),
  getRun: (runId: string) => request<RunSummary>(`/api/runs/${runId}`),
  chat: (runId: string, question: string) =>
    request<{ question: string; answer: ChatAnswer }>(`/api/runs/${runId}/chat`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  latestEval: () =>
    request<{
      available: boolean;
      run_id?: string;
      scorecard?: { summary?: Record<string, unknown> };
      scorecard_md?: string;
    }>("/api/eval/latest"),
  runFile: (runId: string, relative: string) => `${API_BASE}/api/runs/${runId}/file/${relative}`,
};
