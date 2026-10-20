export type PaymentView = {
  state: "pending" | "authorized" | "captured" | "voided";
  amount_minor: number;
  captured_minor: number;
};

export type GuestView = {
  guest_name: string;
  trip_name: string;
  starts_on: string;
  ends_on: string;
  days: number;
  deposit: PaymentView;
  balance: { opens_at: string; amount_minor: number; payment: PaymentView | null };
};

export type Purpose = "deposit" | "balance";

/** GET /api/guest/{token}/settlement: only this guest's lines, no internal ids. */
export type SettlementView = {
  settlements: {
    /** `asked`: the operator is deciding */
    state: "asked" | "accepted" | "executed";
    days: number[];
    whole_trip: boolean;
    cause: string;
    recorded_at: string;
    warnings: {
      source: string;
      event: string;
      area_desc: string;
      onset: string;
      expires: string;
      recorded_at: string;
      replayed: boolean;
      url: string;
    }[];
    outcomes: {
      purpose: "deposit" | "balance";
      kind: "refund" | "credit" | "none";
      amount_minor: number;
      credit_expiry_months: number | null;
      /** the clauses behind the outcome, cut from the operator's wording */
      clauses: string[];
    }[];
    /** built by the backend from the facts above (step 11 may replace it with a model draft) */
    explanation: string;
  }[];
  credits: { purpose: string; amount_minor: number; expires_at: string | null }[];
};
