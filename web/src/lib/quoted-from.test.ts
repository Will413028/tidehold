import { expect, it } from "vitest";
import { quotedFromText } from "./quoted-from";

it("names the quoted operator, its page and the access date", () => {
  const q = {
    name: "Pure Dive Resort",
    url: "https://purediveresort.com/deep-diving/",
    accessed: "2026-10-19",
  };
  expect(quotedFromText(q)).toBe(
    "Quoted from Pure Dive Resort's published policy (https://purediveresort.com/deep-diving/, accessed 2026-10-19).",
  );
});
