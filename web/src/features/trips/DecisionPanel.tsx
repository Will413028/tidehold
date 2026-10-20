"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Option, Proposal } from "./console-types";
import { type Decision, decisionBody, lineText } from "./proposal-view";

const PURPOSES = ["deposit", "balance"] as const;

function OptionChoice({
  purpose,
  options,
  names,
  chosen,
  onChoose,
}: {
  purpose: (typeof PURPOSES)[number];
  options: Option[];
  names: Map<string, string>;
  chosen: number;
  onChoose: (i: number) => void;
}) {
  return (
    <fieldset>
      <legend>{purpose === "deposit" ? "Deposit" : "Balance"}</legend>
      {options.map((option, i) => (
        <label key={option.label}>
          <input type="radio" name={purpose} checked={chosen === i} onChange={() => onChoose(i)} />{" "}
          {option.label}:{" "}
          {option.lines.map((line) => lineText(line, names, purpose)).join("; ") || "no guests"}
        </label>
      ))}
    </fieldset>
  );
}

/** What the operator decides on: the stored question and facts come first; a model draft, when
 * there is one, is shown below them and labelled (threat model, surface 4). */
export function DecisionPanel({
  tripId,
  proposal,
  names,
  onDecided,
}: {
  tripId: string;
  proposal: Proposal;
  names: Map<string, string>;
  onDecided: () => void;
}) {
  const [choice, setChoice] = useState<Record<string, number>>({ deposit: 0, balance: 0 });
  const [error, setError] = useState<string | null>(null);
  const settlement = proposal.kind === "settlement";

  async function decide(decision: Decision) {
    setError(null);
    try {
      const body = decisionBody(proposal, choice, decision);
      await api(`/api/trips/${tripId}/proposals/${proposal.id}/decision`, { body });
      onDecided();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <section aria-label="Decision">
      <p>{proposal.question}</p>
      {proposal.reasons.length > 0 && <p>Why it asks: {proposal.reasons.join(", ")}</p>}
      {proposal.draft && (
        <p>
          <small>Drafted by the model:</small> {proposal.draft}
        </p>
      )}
      {settlement &&
        PURPOSES.map(
          (purpose) =>
            proposal.parts[purpose] && (
              <OptionChoice
                key={purpose}
                purpose={purpose}
                options={proposal.parts[purpose] ?? []}
                names={names}
                chosen={choice[purpose] ?? 0}
                onChoose={(i) => setChoice((all) => ({ ...all, [purpose]: i }))}
              />
            ),
        )}
      {settlement ? (
        <>
          <button type="button" onClick={() => void decide("accept")}>
            Accept these options
          </button>
          <button type="button" onClick={() => void decide("reject")}>
            Reject: withdraw the cancellation, no money moves
          </button>
        </>
      ) : (
        <button type="button" onClick={() => void decide("dismiss")}>
          Dismiss this ask
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
