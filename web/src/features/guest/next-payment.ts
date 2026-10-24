import type { GuestView, Purpose } from "./types";

/** The one payment the guest can make now: the deposit first, then the balance; none for what
 * the operator's settlements left nothing due on, or once the operator released the guest. */
export function nextPayment(view: GuestView): Purpose | null {
  if (view.deposit.state === "pending" && view.deposit.due_minor > 0) return "deposit";
  const balance = view.balance.payment;
  if (!view.released && balance.state === "pending" && balance.due_minor > 0) return "balance";
  return null;
}
