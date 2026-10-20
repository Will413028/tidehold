import type { Rule } from "./types";

/** A rule the operator writes by hand: its quote must be pasted from the wording. */
export function blankRule(quote: string): Rule {
  return {
    product: null,
    causes: ["weather"],
    warning: null,
    scope: "per_day",
    applies_to: "both",
    remedies: [{ kind: "refund", percent: 100, deductions: [], credit_expiry_months: null }],
    chooser: null,
    quote,
  };
}

export function toggled<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

export function withWarning(rule: Rule, on: boolean, source: string | null): Rule {
  if (!on) return { ...rule, warning: null };
  return { ...rule, warning: rule.warning ?? { named_as: "", source, event_values: [], quote: "" } };
}

/** The publish body: one trimmed answer per item the compilation asked about, by index. */
export function publishBody(
  wording: string,
  compilationId: string | null,
  rules: Rule[],
  answers: Record<number, string>,
) {
  return {
    wording,
    compilation_id: compilationId,
    rules,
    rulings: Object.entries(answers)
      .map(([index, answer]) => ({ index: Number(index), answer: answer.trim() }))
      .sort((a, b) => a.index - b.index),
  };
}
