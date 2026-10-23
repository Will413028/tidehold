"use client";

import { type FormEvent, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { PolicyVersion } from "@/lib/api-types";
import { ensureWorkspace } from "@/lib/workspace";
import { policyChoice, tripBody } from "./trip-form";
import type { Trip } from "./types";

const ZONES = Intl.supportedValuesOf("timeZone");

function PolicyOptions({ versions }: { versions: PolicyVersion[] }) {
  return versions.flatMap((v) => {
    const published = v.created_at.slice(0, 16).replace("T", " ");
    const products = v.products.length > 0 ? v.products : [null];
    return products.map((product) => (
      <option key={policyChoice(v.id, product)} value={policyChoice(v.id, product)}>
        {published} UTC{product ? ` · ${product}` : ""}
      </option>
    ));
  });
}

export function TripForm({
  versions,
  onCreated,
}: {
  versions: PolicyVersion[];
  onCreated: (t: Trip) => void;
}) {
  const [error, setError] = useState<string | null>(null);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Read the form before any await: React clears currentTarget once the handler yields.
    const body = tripBody(new FormData(event.currentTarget));
    setError(null);
    try {
      await ensureWorkspace();
      onCreated(await api<Trip>("/api/trips", { body }));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  if (versions.length === 0) {
    return (
      <p>
        Publish a cancellation policy first: <a href="/policies">Policies</a>.
      </p>
    );
  }
  return (
    <form onSubmit={(e) => void create(e)}>
      <label>
        Name <input name="name" required maxLength={200} />
      </label>
      <label>
        Policy{" "}
        <select name="policy" required>
          <PolicyOptions versions={versions} />
        </select>
      </label>
      <label>
        Starts on <input name="starts_on" type="date" required />
      </label>
      <label>
        Days <input name="days" type="number" min={1} max={21} defaultValue={1} required />
      </label>
      <label>
        Time zone{" "}
        <input
          name="timezone"
          list="zones"
          required
          defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone}
        />
        <datalist id="zones">
          {ZONES.map((zone) => (
            <option key={zone} value={zone} />
          ))}
        </datalist>
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
      <fieldset>
        <legend>Mandate: the most settlements may return without asking you</legend>
        <label>
          Per guest (USD){" "}
          <input name="mandate_guest_usd" type="number" min={0} step={0.01} defaultValue={0} required />
        </label>
        <label>
          Per trip (USD){" "}
          <input name="mandate_trip_usd" type="number" min={0} step={0.01} defaultValue={0} required />
        </label>
      </fieldset>
      <button type="submit">Create trip</button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
