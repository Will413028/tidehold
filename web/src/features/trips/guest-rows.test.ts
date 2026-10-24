import { expect, it } from "vitest";
import { guestRows } from "./guest-rows";

const guest = {
  id: "g-1",
  name: "Ana",
  token: "t-1",
  deposit_state: "captured",
  balance_state: "captured",
  paid_minor: 35000,
  refunded_minor: 11666,
  released: false,
};

it("shows what each guest paid, was refunded and holds as credit", () => {
  const ben = {
    ...guest,
    id: "g-2",
    name: "Ben",
    balance_state: "pending",
    paid_minor: 5000,
    refunded_minor: 0,
  };
  const credits = [
    { guest_id: "g-2", purpose: "deposit", amount_minor: 2500 },
    { guest_id: "g-2", purpose: "balance", amount_minor: 100 },
  ];

  expect(guestRows([guest, ben], credits)).toEqual([
    { ...guest, balance: "captured", paid: "USD 350.00", refunded: "USD 116.66", credited: "USD 0.00" },
    { ...ben, balance: "pending", paid: "USD 50.00", refunded: "USD 0.00", credited: "USD 26.00" },
  ]);
});

it("shows a released guest's balance as released", () => {
  const released = {
    ...guest,
    balance_state: "pending",
    paid_minor: 5000,
    refunded_minor: 0,
    released: true,
  };
  expect(guestRows([released], [])[0]?.balance).toBe("released, nothing more to pay");
});
