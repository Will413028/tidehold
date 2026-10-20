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
  if (balance.payment === null) {
    return `${formatUsd(balance.amount_minor)}, held from ${balance.opens_at.slice(0, 10)} (29 days before your last trip day)`;
  }
  return `${formatUsd(balance.amount_minor)}: ${STATE[balance.payment.state]}`;
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
