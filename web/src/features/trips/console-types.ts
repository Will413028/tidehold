/** The trip page's backend shapes (backend/src/tidehold/settlement/http.py, trips/http.py). */

export type Line = {
  guest_id: string;
  kind: "refund" | "credit" | "none";
  amount_minor: number;
  credit_expiry_months: number | null;
  base_minor: number;
};

export type Option = { label: string; rules: number[]; lines: Line[] };

export type Proposal = {
  id: string;
  kind: string;
  subject: string;
  state: "asked" | "accepted" | "executed" | "rejected" | "dismissed" | "answered";
  cancellation_id: string | null;
  reasons: string[];
  parts: Partial<Record<"deposit" | "balance", Option[]>>;
  choice: Record<string, number> | null;
  decided_by: string | null;
  question: string;
  draft: string | null;
  facts: Record<string, unknown>;
  created_at: string;
  decided_at: string | null;
};

type Warning = {
  source: string;
  identifier: string;
  event: string;
  area_desc: string;
  onset: string;
  expires: string;
  recorded_at: string;
  visible_at: string;
  replayed: boolean;
  url: string;
  trip_days: number[];
};

export type Evidence = {
  warnings: Warning[];
  forecast: {
    status: "ok" | "unavailable";
    attribution: string;
    fetched_at: string | null;
    url: string | null;
    days: { day: string; max_wave_height_m: string | null }[];
  };
};

export type Recorded = {
  id: string;
  identifier: string;
  event: string;
  headline: string | null;
  area_desc: string;
  area_codes: string[];
  onset: string;
  expires: string;
  recorded_at: string;
};

export type FailedAction = {
  kind: string;
  subject: string;
  failures: number;
  last_error: string | null;
  finished_at: string;
};

export type TimelineEvent = {
  at: string;
  guest: string;
  purpose: string;
  kind: string;
  detail: Record<string, unknown>;
};
