import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { GuestTrip } from "./GuestTrip";
import { nextPayment } from "./next-payment";
import type { GuestView } from "./types";

vi.mock("./GuestPayments", () => ({ GuestPayments: ({ purpose }: { purpose: string }) => `pay-${purpose}` }));

const VIEW: GuestView = {
  guest_name: "Ana",
  trip_name: "Anilao weekend",
  starts_on: "2026-12-05",
  ends_on: "2026-12-07",
  days: 3,
  deposit: { state: "pending", amount_minor: 5000, captured_minor: 0 },
  balance: { opens_at: "2026-11-09T00:00:00Z", amount_minor: 30000, payment: null },
};

it("shows the trip, both payments and the sandbox notice", () => {
  const html = renderToStaticMarkup(<GuestTrip token="t" view={VIEW} />);

  expect(html).toContain("Anilao weekend");
  expect(html).toContain("Dec 5, 2026 – Dec 7, 2026 · 3 days");
  expect(html).toContain("Deposit: USD 50.00: not paid yet");
  expect(html).toContain("held from 2026-11-09");
  expect(html).toContain("do not enter real personal data");
  expect(html).toContain("pay-deposit");
});

it("offers the deposit first, then the balance once it opens, then nothing", () => {
  const paid = { ...VIEW, deposit: { ...VIEW.deposit, state: "captured" as const } };
  const open = {
    ...paid,
    balance: {
      ...VIEW.balance,
      payment: { state: "pending" as const, amount_minor: 30000, captured_minor: 0 },
    },
  };
  const held = {
    ...open,
    balance: { ...open.balance, payment: { ...open.balance.payment, state: "authorized" as const } },
  };

  expect([nextPayment(VIEW), nextPayment(paid), nextPayment(open), nextPayment(held)]).toEqual([
    "deposit",
    null,
    "balance",
    null,
  ]);
});
