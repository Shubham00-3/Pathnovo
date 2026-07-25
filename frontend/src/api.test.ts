import { describe, expect, it } from "vitest";

import { formatApiError } from "./api";

describe("formatApiError", () => {
  it("extracts structured backend messages", () => {
    expect(
      formatApiError(
        { detail: { message: "Pair mismatch", details: { reasons: ["different document"] } } },
        "fallback",
      ),
    ).toContain("Pair mismatch");
  });

  it("keeps validation errors readable", () => {
    expect(
      formatApiError({ detail: [{ msg: "field required" }] }, "fallback"),
    ).toBe("field required");
  });
});
