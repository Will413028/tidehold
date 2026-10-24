import { expect, it } from "vitest";
import type { Proposal } from "./console-types";
import { askTitle, decisionBody, lineText } from "./proposal-view";

const NAMES = new Map([["g-1", "Ana"]]);

it("writes each guest's line of an option with code-computed amounts", () => {
  const line = { guest_id: "g-1", amount_minor: 1666, credit_expiry_months: null, base_minor: 5000 };
  expect(lineText({ ...line, kind: "refund" }, NAMES)).toBe("Ana: refund USD 16.66");
  expect(lineText({ ...line, kind: "credit", credit_expiry_months: 12 }, NAMES)).toBe(
    "Ana: credit USD 16.66, expires after 12 months",
  );
  expect(lineText({ ...line, kind: "none" }, NAMES)).toBe("Ana: nothing");
  expect(lineText({ ...line, kind: "refund", guest_id: "g-9" }, NAMES)).toBe("a guest: refund USD 16.66");
});

const base: Proposal = {
  id: "p-1",
  kind: "settlement",
  subject: "cancellation:c-1",
  state: "asked",
  cancellation_id: "c-1",
  reasons: [],
  parts: {},
  choice: null,
  decided_by: null,
  question: "Q?",
  draft: null,
  facts: {},
  created_at: "2026-10-20T00:00:00Z",
  decided_at: null,
};

it("titles each kind of ask from its stored facts", () => {
  const settlement = {
    ...base,
    facts: { cancellation: { covered_days: [2, 3], whole_trip: false, cause: "weather" } },
  };
  const whole = {
    ...base,
    facts: { cancellation: { covered_days: [1, 2], whole_trip: true, cause: "safety" } },
  };
  const warning = { ...base, kind: "warning", facts: { warning: { event: "Gale Warning", trip_day: 1 } } };
  const guest = { ...base, kind: "unpaid_balance", facts: { guest: "Ana" } };
  const other = { ...base, kind: "money_refused", question: "PayPal refused the refund. Settle it." };

  expect([settlement, whole, warning, guest, other].map(askTitle)).toEqual([
    "Cancelled days 2 to 3 (weather)",
    "Cancelled the whole trip (safety)",
    "Gale Warning in force on day 1",
    "Ana has not paid the balance",
    "PayPal refused the refund.",
  ]);
});

it("sends a settlement's chosen options, rejects a settlement and dismisses other asks", () => {
  expect(decisionBody(base, { deposit: 1, balance: 0 }, "accept")).toEqual({
    accept: { deposit: 1, balance: 0 },
  });
  expect(decisionBody(base, {}, "reject")).toEqual({ reject: true });
  expect(decisionBody({ ...base, kind: "warning" }, {}, "dismiss")).toEqual({ dismiss: true });
});
