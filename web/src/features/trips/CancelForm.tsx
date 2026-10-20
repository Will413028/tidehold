"use client";

import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Trip } from "./types";

/** The operator's cancellation is what starts a settlement (D16); a warning never does. */
export function CancelForm({ trip, onDone }: { trip: Trip; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);

  async function cancel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError(null);
    try {
      const body = {
        scope: String(form.get("scope")),
        trip_day: Number(form.get("trip_day")),
        cause: String(form.get("cause")),
      };
      await api(`/api/trips/${trip.id}/cancellations`, { body });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <section>
      <h2>Record a cancellation</h2>
      <form onSubmit={(e) => void cancel(e)}>
        <label>
          What{" "}
          <select name="scope">
            <option value="day">one trip day</option>
            <option value="rest">the rest of the trip from that day</option>
          </select>
        </label>
        <label>
          Trip day{" "}
          <select name="trip_day">
            {Array.from({ length: trip.days }, (_, i) => i + 1).map((day) => (
              <option key={day} value={day}>
                {day}
              </option>
            ))}
          </select>
        </label>
        <label>
          Cause{" "}
          <select name="cause">
            <option value="weather">weather</option>
            <option value="safety">safety</option>
            <option value="other">other</option>
          </select>
        </label>
        <button type="submit">Record the cancellation</button>
        {error && <p role="alert">{error}</p>}
      </form>
    </section>
  );
}
