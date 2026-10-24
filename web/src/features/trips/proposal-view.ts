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
  const move = p.facts as {
    guest?: string;
    purpose?: string;
    kind?: string;
    amount_minor?: number;
    code?: string;
  };
  const amount = formatUsd(move.amount_minor ?? 0);
  if (p.kind === "money_refused" && move.code === "REFUND_WINDOW_CLOSED") {
    return `${move.guest}'s ${move.kind} of ${amount} is past PayPal's refund window`;
  }
  if (p.kind === "command_open")
    return `Did ${move.guest}'s ${move.purpose} ${move.kind} of ${amount} reach PayPal?`;
  return `${p.question.split(". ")[0].replace(/\.$/, "")}.`;
}

export type Decision = "accept" | "reject" | "dismiss" | "release" | "found" | "not_found";

type Answer = { decision: Decision; label: string };

const DISMISS: Answer[] = [{ decision: "dismiss", label: "Dismiss this ask" }];

/** The answers each kind of ask takes (backend `ANSWERS`, settlement/proposals.py). */
const ANSWERS: Record<string, Answer[]> = {
  settlement: [
    { decision: "accept", label: "Accept these options" },
    { decision: "reject", label: "Reject: withdraw the cancellation, no money moves" },
  ],
  warning: DISMISS,
  money_refused: DISMISS,
  // waiting is the dismiss answer: the ask closes and the guest's link stays open
  unpaid_balance: [
    { decision: "dismiss", label: "Wait: the guest's link stays open" },
    { decision: "release", label: "Release the guest: nothing more is charged" },
  ],
  command_open: [
    { decision: "found", label: "Found at PayPal" },
    { decision: "not_found", label: "Not found at PayPal" },
  ],
};

export function answers(p: Proposal): Answer[] {
  return ANSWERS[p.kind] ?? DISMISS;
}

export function decisionBody(choice: Record<string, number>, decision: Decision) {
  switch (decision) {
    case "accept":
      return { accept: { deposit: choice.deposit ?? 0, balance: choice.balance ?? 0 } };
    case "release":
      return { release: true };
    case "found":
    case "not_found":
      return { resolve: decision };
    case "reject":
      return { reject: true };
    case "dismiss":
      return { dismiss: true };
  }
}
