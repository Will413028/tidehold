import { formatUsd } from "@/lib/money";
import type { Credit } from "./console-types";
import type { Trip } from "./types";

type Guest = Trip["guests"][number];

export type GuestRow = Guest & { balance: string; paid: string; refunded: string; credited: string };

/** The guests grid's rows: amounts over both payments, credit from the trip's ledger. */
export function guestRows(guests: Guest[], credits: Pick<Credit, "guest_id" | "amount_minor">[]): GuestRow[] {
  const credited = new Map<string, number>();
  for (const c of credits) credited.set(c.guest_id, (credited.get(c.guest_id) ?? 0) + c.amount_minor);
  return guests.map((g) => ({
    ...g,
    balance: g.released ? "released, nothing more to pay" : (g.balance_state ?? "no payment"),
    paid: formatUsd(g.paid_minor),
    refunded: formatUsd(g.refunded_minor),
    credited: formatUsd(credited.get(g.id) ?? 0),
  }));
}
