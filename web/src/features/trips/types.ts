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
  site_lat: number;
  site_lon: number;
  warning_source: string;
  warning_areas: string[];
  deposit_minor: number;
  balance_minor: number;
  balance_due_days: number;
  policy_version_id: string;
  policy_product: string | null;
  timezone: string;
  mandate_guest_minor: number;
  mandate_trip_minor: number;
  guests: GuestRow[];
};
