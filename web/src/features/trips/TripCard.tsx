"use client";

import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { formatDateRange } from "@/lib/dates";
import { formatUsd } from "@/lib/money";
import type { Trip } from "./types";

export function TripCard({ trip, onChange }: { trip: Trip; onChange: (trip: Trip) => void }) {
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
    <article>
      <h2>{trip.name}</h2>
      <p>
        {formatDateRange(trip.starts_on, trip.ends_on)} · deposit {formatUsd(trip.deposit_minor)} · balance{" "}
        {formatUsd(trip.balance_minor)} · warnings: {trip.warning_source} {trip.warning_areas.join(", ")} ·
        balance hold {trip.balance_requested_at ? "open" : "not open yet"}
      </p>
      <ul>
        {trip.guests.map((guest) => (
          <li key={guest.id}>
            {guest.name}: deposit {guest.deposit_state}, balance {guest.balance_state ?? "not open"} ·{" "}
            <a href={`/g/${guest.token}`}>guest link</a>
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => void invite(e)}>
        <label>
          Invite guest <input name="name" required maxLength={80} />
        </label>
        <button type="submit">Invite</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </article>
  );
}
