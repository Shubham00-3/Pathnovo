import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App", () => {
  it("loads the API-backed pair controls and accessible tabs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/health")) {
          return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
        }
        if (url.endsWith("/api/pids")) {
          return new Response(
            JSON.stringify({
              pids: [
                { pid: "PID-SYN-A", revision_label: "A" },
                { pid: "PID-SYN-B", revision_label: "B" },
              ],
            }),
            { status: 200 },
          );
        }
        return new Response("not found", { status: 404 });
      }),
    );

    render(<App />);

    expect(
      screen.getByRole("heading", { name: /Document Delta & Grounded Chat/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Pair setup" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() => {
      expect(screen.getByLabelText("PID A (base)")).toHaveValue("PID-SYN-A");
      expect(screen.getByLabelText("PID B (revised)")).toHaveValue("PID-SYN-B");
    });
  });
});
