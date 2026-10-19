type GuestRow = {
  id: string;
  name: string;
  token: string;
  deposit_state: string;
  balance_state: string | null;
};

export type Trip = {
  id: string;
  name: string;
  starts_on: string;
  ends_on: string;
  days: number;
  warning_source: string;
  warning_areas: string[];
  deposit_minor: number;
  balance_minor: number;
  balance_requested_at: string | null;
  guests: GuestRow[];
};
