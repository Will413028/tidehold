/** Mirrors backend/src/tidehold/policy/schema.py; the API checks every field on publish. */
export type Cause = "weather" | "safety" | "any";

export type Remedy = {
  kind: "refund" | "credit" | "none";
  percent: number;
  deductions: { kind: "percent" | "fixed"; value: number; label: string }[];
  credit_expiry_months: number | null;
};

export type WarningCondition = {
  named_as: string;
  source: string | null;
  event_values: string[];
  quote: string;
};

export type Rule = {
  product: string | null;
  causes: Cause[];
  warning: WarningCondition | null;
  scope: "per_day" | "per_trip";
  applies_to: "deposit" | "balance" | "both";
  remedies: Remedy[];
  chooser: "guest" | "operator" | null;
  quote: string;
};

export type Asked = {
  index: number;
  topic: string;
  question: string;
  quote: string | null;
  clause: string | null;
};

export type Compilation = {
  id: string | null;
  mode: "live" | "recorded" | "no_ai";
  reason: string | null;
  model: string | null;
  wording: string;
  rules: { rule: Rule; clause: string; warning_clause: string | null }[];
  needs_ruling: Asked[];
};

export type EventSetProposal = {
  mode: "live" | "recorded" | "no_ai";
  reason: string | null;
  values: string[];
  /** the source's event values by hazard, least to most severe */
  catalogue: string[][];
};
