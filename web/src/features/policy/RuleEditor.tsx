"use client";

import { useState } from "react";
import { EventSetPicker } from "./EventSetPicker";
import { RemedyEditor } from "./RemedyEditor";
import { toggled, withWarning } from "./rules";
import type { Cause, Rule, WarningCondition } from "./types";

const CAUSES: Cause[] = ["weather", "safety", "any"];
const SOURCES = ["pagasa", "nws", "meteoalarm", "jma"];

function WarningEditor({
  warning,
  onChange,
}: {
  warning: WarningCondition;
  onChange: (w: WarningCondition) => void;
}) {
  return (
    <fieldset>
      <legend>Official warning condition</legend>
      <label>
        Named in the wording as{" "}
        <input
          value={warning.named_as}
          onChange={(e) => onChange({ ...warning, named_as: e.target.value })}
        />
      </label>
      <label>
        Source{" "}
        <select
          value={warning.source ?? ""}
          onChange={(e) => onChange({ ...warning, source: e.target.value || null, event_values: [] })}
        >
          <option value="">(choose)</option>
          {SOURCES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label>
        Warning quote (verbatim){" "}
        <textarea value={warning.quote} onChange={(e) => onChange({ ...warning, quote: e.target.value })} />
      </label>
      <EventSetPicker warning={warning} onChange={(event_values) => onChange({ ...warning, event_values })} />
    </fieldset>
  );
}

export function RuleEditor({
  rule,
  clause,
  onChange,
  onRemove,
}: {
  rule: Rule;
  /** the clause the API cut from the wording, for a compiled rule */
  clause: string | null;
  onChange: (rule: Rule) => void;
  onRemove: () => void;
}) {
  // remedies carry no id and this editor never adds or reorders them: one key each, kept
  const [keys] = useState(() => rule.remedies.map(() => crypto.randomUUID()));
  return (
    <article aria-label="Rule">
      {clause && <blockquote>{clause}</blockquote>}
      <label>
        Quote (verbatim from the wording){" "}
        <textarea
          required
          value={rule.quote}
          onChange={(e) => onChange({ ...rule, quote: e.target.value })}
        />
      </label>
      <label>
        Product heading{" "}
        <input
          value={rule.product ?? ""}
          placeholder="none"
          onChange={(e) => onChange({ ...rule, product: e.target.value || null })}
        />
      </label>
      <fieldset>
        <legend>Operator cancellation causes</legend>
        {CAUSES.map((cause) => (
          <label key={cause}>
            <input
              type="checkbox"
              checked={rule.causes.includes(cause)}
              onChange={() => onChange({ ...rule, causes: toggled(rule.causes, cause) })}
            />{" "}
            {cause}
          </label>
        ))}
      </fieldset>
      <label>
        Scope{" "}
        <select
          value={rule.scope}
          onChange={(e) => onChange({ ...rule, scope: e.target.value as Rule["scope"] })}
        >
          <option value="per_day">per day</option>
          <option value="per_trip">per trip</option>
        </select>
      </label>
      <label>
        Applies to{" "}
        <select
          value={rule.applies_to}
          onChange={(e) => onChange({ ...rule, applies_to: e.target.value as Rule["applies_to"] })}
        >
          <option value="both">deposit and balance</option>
          <option value="deposit">deposit</option>
          <option value="balance">balance</option>
        </select>
      </label>
      {rule.remedies.map((remedy, i) => (
        <RemedyEditor
          key={keys[i]}
          remedy={remedy}
          onChange={(r) => onChange({ ...rule, remedies: rule.remedies.map((x, j) => (j === i ? r : x)) })}
        />
      ))}
      {rule.remedies.length > 1 && (
        <label>
          Who chooses{" "}
          <select
            value={rule.chooser ?? ""}
            onChange={(e) => onChange({ ...rule, chooser: (e.target.value || null) as Rule["chooser"] })}
          >
            <option value="">(choose)</option>
            <option value="guest">the guest</option>
            <option value="operator">the operator</option>
          </select>
        </label>
      )}
      <label>
        <input
          type="checkbox"
          checked={rule.warning !== null}
          onChange={(e) => onChange(withWarning(rule, e.target.checked, null))}
        />{" "}
        Requires an official warning
      </label>
      {rule.warning && (
        <WarningEditor warning={rule.warning} onChange={(w) => onChange({ ...rule, warning: w })} />
      )}
      <button type="button" onClick={onRemove}>
        Remove this rule
      </button>
    </article>
  );
}
