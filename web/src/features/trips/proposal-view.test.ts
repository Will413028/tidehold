import { expect, it } from "vitest";
import type { Proposal } from "./console-types";
import { answers, askTitle, decisionBody, lineText } from "./proposal-view";

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
  const late = {
    ...base,
    kind: "money_refused",
    facts: { guest: "Ana", kind: "refund", amount_minor: 15000, code: "REFUND_WINDOW_CLOSED" },
  };
  const other = { ...base, kind: "money_refused", question: "PayPal refused the refund. Settle it." };
  const open = {
    ...base,
    kind: "command_open",
    facts: { guest: "Ana", purpose: "balance", kind: "refund", amount_minor: 15000 },
  };

  expect([settlement, whole, warning, guest, late, other, open].map(askTitle)).toEqual([
    "Cancelled days 2 to 3 (weather)",
    "Cancelled the whole trip (safety)",
    "Gale Warning in force on day 1",
    "Ana has not paid the balance",
    "Ana's refund of USD 150.00 is past PayPal's refund window",
    "PayPal refused the refund.",
    "Did Ana's balance refund of USD 150.00 reach PayPal?",
  ]);
});

it("sends each answer in the body the decision route takes", () => {
  expect(decisionBody({ deposit: 1, balance: 0 }, "accept")).toEqual({
    accept: { deposit: 1, balance: 0 },
  });
  expect(decisionBody({}, "reject")).toEqual({ reject: true });
  expect(decisionBody({}, "dismiss")).toEqual({ dismiss: true });
  expect(decisionBody({}, "release")).toEqual({ release: true });
  expect(decisionBody({}, "found")).toEqual({ resolve: "found" });
  expect(decisionBody({}, "not_found")).toEqual({ resolve: "not_found" });
});

it("offers each kind of ask only the answers the backend takes for it", () => {
  const offered = (kind: string) => answers({ ...base, kind }).map((a) => a.decision);
  expect(offered("settlement")).toEqual(["accept", "reject"]);
  expect(offered("warning")).toEqual(["dismiss"]);
  expect(offered("money_refused")).toEqual(["dismiss"]);
  // waiting on an unpaid balance is the dismiss answer (settlement/proposals.py ANSWERS)
  expect(offered("unpaid_balance")).toEqual(["dismiss", "release"]);
  // an open command is never dismissed: that would leave it open for ever
  expect(offered("command_open")).toEqual(["found", "not_found"]);
  expect(answers({ ...base, kind: "unpaid_balance" }).map((a) => a.label)).toEqual([
    "Wait: the guest's link stays open",
    "Release the guest: nothing more is charged",
  ]);
});
