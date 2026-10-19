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
