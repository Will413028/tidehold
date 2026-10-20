"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { recordedLabel } from "@/lib/evidence";
import type { Evidence, Recorded } from "./console-types";
import type { Trip } from "./types";

function ReplayPicker({ trip, onReplayed }: { trip: Trip; onReplayed: (e: Evidence) => void }) {
  const [recorded, setRecorded] = useState<Recorded[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const query = new URLSearchParams({ source: trip.warning_source });
    api<Recorded[]>(`/api/recorded-warnings?${query}`)
      .then(setRecorded)
      .catch(() => setRecorded([]));
  }, [trip.warning_source]);

  async function replay(form: FormData) {
    setError(null);
    try {
      const body = { warning_id: String(form.get("warning_id")) };
      onReplayed(await api<Evidence>(`/api/trips/${trip.id}/replays`, { body }));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  if (recorded.length === 0) return <p>No recorded {trip.warning_source} warning to replay.</p>;
  return (
    <form action={(form) => void replay(form)}>
      <label>
        Recorded warning{" "}
        <select name="warning_id">
          {recorded.map((w) => (
            <option key={w.id} value={w.id}>
              {w.event} · {w.area_codes.slice(0, 3).join(", ")} · recorded {w.recorded_at.slice(0, 16)} UTC
            </option>
          ))}
        </select>
      </label>
      <button type="submit">Replay it as if fetched now</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}

export function EvidenceSection({
  trip,
  evidence,
  onReplayed,
}: {
  trip: Trip;
  evidence: Evidence | null;
  onReplayed: (e: Evidence) => void;
}) {
  return (
    <section>
      <h2>Evidence</h2>
      <ul aria-label="Warnings in force">
        {evidence?.warnings.length === 0 && <li>No {trip.warning_source} warning in force on a trip day.</li>}
        {evidence?.warnings.map((w) => (
          <li key={`${w.identifier}:${w.replayed}`}>
            <a href={w.url}>{w.event}</a> · {w.area_desc} · trip days {w.trip_days.join(", ")} ·{" "}
            {recordedLabel(w)}
          </li>
        ))}
      </ul>
      {evidence && (
        <p>
          {evidence.forecast.status === "ok"
            ? `Max wave height: ${evidence.forecast.days
                .map((d) => `${d.day} ${d.max_wave_height_m ?? "no forecast yet"} m`)
                .join(" · ")}`
            : "Forecast unavailable."}{" "}
          <small>{evidence.forecast.attribution}. Corroborates only: never settles anything.</small>
        </p>
      )}
      <ReplayPicker trip={trip} onReplayed={onReplayed} />
    </section>
  );
}
