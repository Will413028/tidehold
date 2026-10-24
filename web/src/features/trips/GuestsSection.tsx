"use client";

import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { type Column, Grid } from "@/lib/grid";
import type { Credit } from "./console-types";
import { type GuestRow, guestRows } from "./guest-rows";
import type { Trip } from "./types";

// a phone shows the balance state and what was paid; the rest is wide (no sideways scroll)
const COLUMNS: Column<GuestRow>[] = [
  { headerName: "Guest", field: "name" },
  { headerName: "Deposit", field: "deposit_state", wide: true },
  { headerName: "Balance", field: "balance" },
  { headerName: "Paid", field: "paid" },
  { headerName: "Refunded", field: "refunded", wide: true },
  { headerName: "Credited", field: "credited", wide: true },
  {
    headerName: "Link",
    maxWidth: 80,
    cellRenderer: ({ data }: { data?: GuestRow }) => data && <a href={`/g/${data.token}`}>open</a>,
  },
];

export function GuestsSection({
  trip,
  credits,
  onChange,
}: {
  trip: Trip;
  credits: Credit[];
  onChange: (trip: Trip) => void;
}) {
  const [error, setError] = useState<string | null>(null);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setError(null);
    try {
      const name = String(new FormData(form).get("name") ?? "");
      onChange(await api<Trip>(`/api/trips/${trip.id}/guests`, { body: { name } }));
      form.reset();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <section>
      <h2>Guests</h2>
      <Grid label="Guests" rows={guestRows(trip.guests, credits)} columns={COLUMNS} rowId={(g) => g.id} />
      <form onSubmit={(e) => void invite(e)}>
        <label>
          Invite guest <input name="name" required maxLength={80} />
        </label>
        <button type="submit">Invite</button>
        {error && <p role="alert">{error}</p>}
      </form>
    </section>
  );
}
