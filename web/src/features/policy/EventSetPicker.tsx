"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { ensureWorkspace } from "@/lib/workspace";
import { toggled } from "./rules";
import type { EventSetProposal, WarningCondition } from "./types";

/** The operator confirms which of the source's event values the warning condition means
 * (D16): the model may propose some, but only the ticked values are stored. */
export function EventSetPicker({
  warning,
  onChange,
}: {
  warning: WarningCondition;
  onChange: (values: string[]) => void;
}) {
  const [proposal, setProposal] = useState<EventSetProposal | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function propose() {
    setError(null);
    try {
      await ensureWorkspace(); // the first write of a new browser may be this one
      const body = { source: warning.source, quote: warning.quote };
      const answer = await api<EventSetProposal>("/api/event-set-proposals", { body });
      setProposal(answer);
      onChange(answer.values);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <fieldset>
      <legend>Event set: {warning.event_values.length} values confirmed</legend>
      <button type="button" disabled={!warning.source || !warning.quote} onClick={() => void propose()}>
        Propose the event set
      </button>
      {proposal && (
        <p>
          {proposal.mode === "no_ai"
            ? "No model answer: pick the values by hand."
            : "Proposed by the model: confirm or change the ticks."}
        </p>
      )}
      {proposal?.catalogue.map((group) => (
        <div key={group.join("|")}>
          {group.map((value) => (
            <label key={value}>
              <input
                type="checkbox"
                checked={warning.event_values.includes(value)}
                onChange={() => onChange(toggled(warning.event_values, value))}
              />{" "}
              {value}
            </label>
          ))}
        </div>
      ))}
      {error && <p role="alert">{error}</p>}
    </fieldset>
  );
}
