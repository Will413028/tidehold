import { formatDateRange } from "@/lib/dates";
import { formatUsd } from "@/lib/money";
import { GuestPayments } from "./GuestPayments";
import { GuestSettlements } from "./GuestSettlements";
import { nextPayment } from "./next-payment";
import type { GuestView, PaymentView, SettlementView } from "./types";

const STATE: Record<PaymentView["state"], string> = {
  pending: "not paid yet",
  authorized: "held (authorized, not charged)",
  captured: "paid",
  voided: "released",
};

function balanceLine(view: GuestView): string {
  const { balance } = view;
  const payment = balance.payment;
  if (view.released)
    return `${formatUsd(balance.amount_minor)}: released by the operator, nothing more to pay`;
  if (payment.state === "pending") {
    // what is still due: the operator's settlements may have taken cancelled days off it
    if (payment.due_minor === 0) return `${formatUsd(balance.amount_minor)}: nothing to pay`;
    return `${formatUsd(payment.due_minor)}, due ${balance.due_on}`;
  }
  if (payment.state === "captured") return `${formatUsd(payment.captured_minor)}: paid`;
  return `${formatUsd(balance.amount_minor)}: ${STATE[payment.state]}`;
}

export function GuestTrip({
  token,
  view,
  settlement,
}: {
  token: string;
  view: GuestView;
  settlement: SettlementView;
}) {
  const purpose = nextPayment(view);
  return (
    <main>
      <h1>{view.trip_name}</h1>
      <p>
        {view.guest_name} · {formatDateRange(view.starts_on, view.ends_on)} · {view.days}{" "}
        {view.days === 1 ? "day" : "days"}
      </p>
      <ul>
        <li>
          Deposit: {formatUsd(view.deposit.amount_minor)}: {STATE[view.deposit.state]}
        </li>
        <li>Balance: {balanceLine(view)}</li>
      </ul>
      <GuestSettlements view={settlement} />
      {purpose && <GuestPayments token={token} purpose={purpose} />}
    </main>
  );
}
