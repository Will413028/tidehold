"use client";

import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { type Column, Grid } from "@/lib/grid";
import type { Trip } from "./types";

type Guest = Trip["guests"][number];

const COLUMNS: Column<Guest>[] = [
  { headerName: "Guest", field: "name" },
  { headerName: "Deposit", field: "deposit_state" },
  { headerName: "Balance", valueGetter: ({ data }) => data?.balance_state ?? "not open" },
  {
    headerName: "Link",
    cellRenderer: ({ data }: { data?: Guest }) => data && <a href={`/g/${data.token}`}>guest page</a>,
  },
];

export function GuestsSection({ trip, onChange }: { trip: Trip; onChange: (trip: Trip) => void }) {
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
      <Grid label="Guests" rows={trip.guests} columns={COLUMNS} rowId={(g) => g.id} />
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
