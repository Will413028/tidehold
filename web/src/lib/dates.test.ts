import { describe, expect, it } from "vitest";
import { formatDateRange } from "./dates";

describe("formatDateRange", () => {
  it("shows both ends of a multi-day trip", () => {
    expect(formatDateRange("2026-12-05", "2026-12-07")).toBe("Dec 5, 2026 – Dec 7, 2026");
  });

  it("shows a one-day trip once", () => {
    expect(formatDateRange("2026-12-05", "2026-12-05")).toBe("Dec 5, 2026");
  });
});
