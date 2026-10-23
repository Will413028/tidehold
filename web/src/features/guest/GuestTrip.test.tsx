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
  balance: {
    due_on: "2026-11-05",
    amount_minor: 30000,
    payment: { state: "pending", amount_minor: 30000, captured_minor: 0 },
  },
};

it("shows the trip and both payments", () => {
  const html = renderToStaticMarkup(
    <GuestTrip token="t" view={VIEW} settlement={{ settlements: [], credits: [] }} />,
  );

  expect(html).toContain("Anilao weekend");
  expect(html).toContain("Dec 5, 2026 – Dec 7, 2026 · 3 days");
  expect(html).toContain("Deposit: USD 50.00: not paid yet");
  expect(html).toContain("Balance: USD 300.00, due 2026-11-05");
  expect(html).toContain("pay-deposit");
});

it("offers the deposit first, then the balance, then nothing", () => {
  const deposited = { ...VIEW, deposit: { ...VIEW.deposit, state: "captured" as const } };
  const paid = {
    ...deposited,
    balance: {
      ...VIEW.balance,
      payment: { state: "captured" as const, amount_minor: 30000, captured_minor: 30000 },
    },
  };

  expect([nextPayment(VIEW), nextPayment(deposited), nextPayment(paid)]).toEqual([
    "deposit",
    "balance",
    null,
  ]);
});

it("shows a paid balance as paid", () => {
  const paid: GuestView = {
    ...VIEW,
    deposit: { ...VIEW.deposit, state: "captured" },
    balance: { ...VIEW.balance, payment: { state: "captured", amount_minor: 30000, captured_minor: 30000 } },
  };

  const html = renderToStaticMarkup(
    <GuestTrip token="t" view={paid} settlement={{ settlements: [], credits: [] }} />,
  );

  expect(html).toContain("Balance: USD 300.00: paid");
  expect(html).not.toContain("pay-");
});
