import { recordedLabel } from "@/lib/evidence";
import { formatUsd } from "@/lib/money";
import type { SettlementView } from "./types";

type Outcome = SettlementView["settlements"][number]["outcomes"][number];

// what a remedy does to each payment: the deposit is returned, the balance hold released
const DONE: Record<string, string> = {
  "refund:deposit": "refunded",
  "refund:balance": "released",
  "credit:deposit": "as credit",
  "credit:balance": "as credit",
};

/** One payment's outcome, from the stored line computed by code (never from drafted text). */
export function outcomeText(o: Outcome): string {
  const done = DONE[`${o.kind}:${o.purpose}`];
  const result = done && o.amount_minor > 0 ? `${formatUsd(o.amount_minor)} ${done}` : "no change";
  return `Your ${o.purpose}: ${result}`;
}

/** The guest's transparency section (R12): the operator's cancellation, the official warning in
 * force at its decision, the clause behind the outcome and this guest's outcome. */
export function GuestSettlements({ view }: { view: SettlementView }) {
  if (view.settlements.length === 0 && view.credits.length === 0) return null;
  return (
    <section aria-label="Cancellations">
      <h2>Cancellations</h2>
      {view.settlements.map((s) => (
        <article key={s.recorded_at}>
          <p>{s.explanation}</p>
          {s.outcomes.length > 0 && (
            <ul aria-label="Your outcome">
              {s.outcomes.map((o) => (
                <li key={o.purpose}>{outcomeText(o)}</li>
              ))}
            </ul>
          )}
          {s.warnings.length > 0 && (
            <ul aria-label="Official warnings in force">
              {s.warnings.map((w) => (
                <li key={`${w.url}:${w.replayed}`}>
                  <a href={w.url}>{w.event}</a> ({w.source.toUpperCase()}) · {w.area_desc} ·{" "}
                  {recordedLabel(w)}
                </li>
              ))}
            </ul>
          )}
          {s.outcomes
            .filter((o) => o.clauses.length > 0)
            .map((o) => (
              <figure key={o.purpose}>
                <figcaption>The operator's policy behind your {o.purpose}:</figcaption>
                {o.clauses.map((clause) => (
                  <blockquote key={clause}>{clause}</blockquote>
                ))}
              </figure>
            ))}
        </article>
      ))}
      {view.credits.map((c) => (
        <p key={`${c.purpose}:${c.amount_minor}:${c.expires_at}`}>
          Credit: {formatUsd(c.amount_minor)} ({c.purpose})
          {c.expires_at ? `, valid until ${c.expires_at.slice(0, 10)}` : ", no expiry"}
        </p>
      ))}
    </section>
  );
}
