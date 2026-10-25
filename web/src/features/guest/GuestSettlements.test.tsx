import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { GuestSettlements, outcomeText } from "./GuestSettlements";
import type { SettlementView } from "./types";

const VIEW: SettlementView = {
  settlements: [
    {
      state: "accepted",
      days: [1],
      whole_trip: false,
      cause: "weather",
      recorded_at: "2026-10-20T08:00:00Z",
      warnings: [
        {
          source: "nws",
          event: "Gale Warning",
          area_desc: "Icy Strait",
          onset: "2026-10-20T00:00:00Z",
          expires: "2026-10-21T00:00:00Z",
          recorded_at: "2026-10-19T11:00:21Z",
          replayed: true,
          url: "https://api.weather.gov/alerts/x",
        },
      ],
      outcomes: [
        {
          purpose: "deposit",
          kind: "refund",
          amount_minor: 2500,
          credit_expiry_months: null,
          clauses: ["If a Gale Warning is in force and we cancel a dive day, you get a full refund."],
          status: "refunded",
        },
      ],
      explanation: "The operator cancelled day 1 for weather. Gale Warning (NWS) was in force.",
    },
  ],
  credits: [{ purpose: "deposit", amount_minor: 5000, expires_at: "2027-10-20T00:00:00Z" }],
  quoted_from: null,
};

it("shows the operator's cancellation, the warning with its recorded label, the clause and the outcome", () => {
  const html = renderToStaticMarkup(<GuestSettlements view={VIEW} />);

  expect(html).toContain("The operator cancelled day 1 for weather. Gale Warning (NWS) was in force.");
  // amounts come from the stored lines, not from the sentence (step 11 may draft the sentence)
  expect(html).toContain("Your deposit: USD 25.00 refunded");
  expect(html).toContain("Gale Warning");
  expect(html).toContain("recorded at 2026-10-19 11:00 UTC, replayed");
  expect(html).toContain("If a Gale Warning is in force and we cancel a dive day, you get a full refund.");
  expect(html).toContain("Credit: USD 50.00 (deposit), valid until 2027-10-20");
});

it("attributes a quoted real operator's clause, and adds nothing for an operator's own", () => {
  const quoted_from = {
    name: "Pure Dive Resort",
    url: "https://purediveresort.com/deep-diving/",
    accessed: "2026-10-19",
  };
  const html = renderToStaticMarkup(<GuestSettlements view={{ ...VIEW, quoted_from }} />);

  expect(html).toContain(
    "Quoted from Pure Dive Resort&#x27;s published policy (https://purediveresort.com/deep-diving/, accessed 2026-10-19).",
  );
  expect(renderToStaticMarkup(<GuestSettlements view={VIEW} />)).not.toContain("Quoted from");
});

it("shows nothing when the operator has cancelled nothing", () => {
  expect(
    renderToStaticMarkup(<GuestSettlements view={{ settlements: [], credits: [], quoted_from: null }} />),
  ).toBe("");
});

it("words an outcome by what it has done so far", () => {
  const base = { amount_minor: 15000, credit_expiry_months: null, clauses: [] };
  expect([
    outcomeText({ ...base, purpose: "balance", kind: "refund", status: "not_charged" }),
    outcomeText({ ...base, purpose: "deposit", kind: "refund", status: "refund_processing" }),
    outcomeText({ ...base, purpose: "balance", kind: "credit", status: "waiting_for_payment" }),
    outcomeText({ ...base, purpose: "balance", kind: "refund", status: "refunded" }),
  ]).toEqual([
    "Your balance: USD 150.00 taken off what you owe",
    "Your deposit: USD 150.00 refund in progress",
    "Your balance: USD 150.00 as credit once you pay",
    "Your balance: USD 150.00 refunded",
  ]);
});

it("words each outcome from its kind and payment", () => {
  const base = { amount_minor: 15000, credit_expiry_months: null, clauses: [], status: "none" as const };
  expect([
    outcomeText({ ...base, purpose: "balance", kind: "refund" }),
    outcomeText({ ...base, purpose: "deposit", kind: "credit", credit_expiry_months: 12 }),
    outcomeText({ ...base, purpose: "deposit", kind: "none" }),
    outcomeText({ ...base, purpose: "deposit", kind: "refund", amount_minor: 0 }),
  ]).toEqual([
    "Your balance: USD 150.00 refunded",
    "Your deposit: USD 150.00 as credit",
    "Your deposit: no change",
    "Your deposit: no change",
  ]);
});
