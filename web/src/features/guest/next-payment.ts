import type { GuestView, Purpose } from "./types";

/** The one payment the guest can make now: the deposit first, then the balance. */
export function nextPayment(view: GuestView): Purpose | null {
  if (view.deposit.state === "pending") return "deposit";
  if (view.balance.payment?.state === "pending") return "balance";
  return null;
}
