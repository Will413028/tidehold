import { formatUsd } from "@/lib/money";
import type { Line, Proposal } from "./console-types";

/** One guest's part of an option; amounts come from the proposal, computed by code. */
export function lineText(line: Line, names: Map<string, string>): string {
  const who = names.get(line.guest_id) ?? "a guest";
  if (line.kind === "none" || line.amount_minor === 0) return `${who}: nothing`;
  if (line.kind === "credit") {
    const expiry = line.credit_expiry_months ? `, expires after ${line.credit_expiry_months} months` : "";
    return `${who}: credit ${formatUsd(line.amount_minor)}${expiry}`;
  }
  // a refund of a paid payment, or taken off what is still due on an unpaid one (step 8b)
  return `${who}: refund ${formatUsd(line.amount_minor)}`;
}

type Cancellation = { covered_days: number[]; whole_trip: boolean; cause: string };

function cancelled(c: Cancellation): string {
  if (c.whole_trip) return "the whole trip";
  const [first, ...rest] = c.covered_days;
  return rest.length === 0 ? `day ${first}` : `days ${first} to ${rest[rest.length - 1]}`;
}

/** A short title from the stored facts; the template question and any draft sit below it. */
export function askTitle(p: Proposal): string {
  const facts = p.facts as Record<string, Record<string, unknown> | string | undefined>;
  if (p.kind === "settlement" && facts.cancellation) {
    const c = facts.cancellation as unknown as Cancellation;
    return `Cancelled ${cancelled(c)} (${c.cause})`;
  }
  if (p.kind === "warning" && facts.warning) {
    const w = facts.warning as { event: string; trip_day: number };
    return `${w.event} in force on day ${w.trip_day}`;
  }
  if (p.kind === "unpaid_balance") return `${String(facts.guest)} has not paid the balance`;
  return `${p.question.split(". ")[0].replace(/\.$/, "")}.`;
}

export type Decision = "accept" | "reject" | "dismiss";

export function decisionBody(p: Proposal, choice: Record<string, number>, decision: Decision) {
  if (decision === "accept" && p.kind === "settlement") {
    return { accept: { deposit: choice.deposit ?? 0, balance: choice.balance ?? 0 } };
  }
  return decision === "reject" ? { reject: true } : { dismiss: true };
}
