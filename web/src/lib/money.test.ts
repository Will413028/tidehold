import { expect, it } from "vitest";
import { formatUsd } from "./money";

it("formats minor units as USD with two decimals", () => {
  expect(formatUsd(30005)).toBe("USD 300.05");
  expect(formatUsd(7)).toBe("USD 0.07");
});
