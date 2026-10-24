export type PaymentView = {
  state: "pending" | "authorized" | "captured" | "voided";
  amount_minor: number;
  /** what is still to pay: the amount less what settlements took off it before it was paid */
  due_minor: number;
  captured_minor: number;
  refunded_minor: number;
};

export type GuestView = {
  guest_name: string;
  trip_name: string;
  starts_on: string;
  ends_on: string;
  days: number;
  deposit: PaymentView;
  /** `due_on` is a local date in the trip's time zone */
  balance: { due_on: string; amount_minor: number; payment: PaymentView };
  /** the operator released the guest from its unpaid balance: nothing more is paid */
  released: boolean;
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
      /** what the outcome did to the payment so far (step 8b) */
      status:
        | "refunded"
        | "refund_processing"
        | "refund_refused"
        | "not_charged"
        | "credited"
        | "waiting_for_payment"
        | "released"
        | "none";
    }[];
    /** built by the backend from the facts above (step 11 may replace it with a model draft) */
    explanation: string;
  }[];
  credits: { purpose: string; amount_minor: number; expires_at: string | null }[];
};
