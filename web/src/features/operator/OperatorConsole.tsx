"use client";

import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { TripCard } from "./TripCard";
import { tripBody } from "./trip-form";
import type { Trip } from "./types";

async function ensureWorkspace(): Promise<void> {
  try {
    await api("/api/workspaces/me");
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) throw e;
    await api("/api/workspaces", { body: {} });
  }
}

export function OperatorConsole() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Trip[]>("/api/trips")
      .then(setTrips)
      .catch(() => setTrips([])); // no workspace yet: it is created with the first trip
  }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Read the form before any await: React clears currentTarget once the handler yields.
    const body = tripBody(new FormData(event.currentTarget));
    setError(null);
    try {
      await ensureWorkspace();
      const trip = await api<Trip>("/api/trips", { body });
      setTrips((all) => [...all, trip]);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  const replace = (trip: Trip) => setTrips((all) => all.map((t) => (t.id === trip.id ? trip : t)));

  return (
    <main>
      <p role="note">Sandbox demo: do not enter real personal data. No real money moves.</p>
      <h1>New trip</h1>
      <form onSubmit={(e) => void create(e)}>
        <label>
          Name <input name="name" required maxLength={200} />
        </label>
        <label>
          Starts on <input name="starts_on" type="date" required />
        </label>
        <label>
          Days <input name="days" type="number" min={1} max={10} defaultValue={1} required />
        </label>
        <label>
          Site latitude <input name="site_lat" type="number" step="any" min={-90} max={90} required />
        </label>
        <label>
          Site longitude <input name="site_lon" type="number" step="any" min={-180} max={180} required />
        </label>
        <label>
          Warning source{" "}
          <select name="warning_source" required>
            <option value="pagasa">PAGASA</option>
            <option value="nws">NWS</option>
            <option value="meteoalarm">MeteoAlarm</option>
            <option value="jma">JMA</option>
          </select>
        </label>
        <label>
          Warning area codes <input name="warning_areas" required placeholder="comma separated" />
        </label>
        <label>
          Deposit (USD) <input name="deposit_usd" type="number" min={0.01} step={0.01} required />
        </label>
        <label>
          Balance (USD) <input name="balance_usd" type="number" min={0.01} step={0.01} required />
        </label>
        <button type="submit">Create trip</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {trips.map((trip) => (
        <TripCard key={trip.id} trip={trip} onChange={replace} />
      ))}
    </main>
  );
}
