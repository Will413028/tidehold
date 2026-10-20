import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { GuestSettlements } from "./GuestSettlements";
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
        },
      ],
      explanation: "The operator cancelled day 1 for weather. Your deposit: USD 25.00 refunded.",
    },
  ],
  credits: [{ purpose: "deposit", amount_minor: 5000, expires_at: "2027-10-20T00:00:00Z" }],
};

it("shows the operator's cancellation, the warning with its recorded label, the clause and the outcome", () => {
  const html = renderToStaticMarkup(<GuestSettlements view={VIEW} />);

  expect(html).toContain("The operator cancelled day 1 for weather. Your deposit: USD 25.00 refunded.");
  expect(html).toContain("Gale Warning");
  expect(html).toContain("recorded at 2026-10-19 11:00 UTC, replayed");
  expect(html).toContain("If a Gale Warning is in force and we cancel a dive day, you get a full refund.");
  expect(html).toContain("Credit: USD 50.00 (deposit), valid until 2027-10-20");
});

it("shows nothing when the operator has cancelled nothing", () => {
  expect(renderToStaticMarkup(<GuestSettlements view={{ settlements: [], credits: [] }} />)).toBe("");
});
