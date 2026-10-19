"use client";

import { type FormEvent, useState } from "react";

type CreatedTrip = { name: string; guest_token: string };

async function ensureWorkspace(): Promise<void> {
  const me = await fetch("/api/workspaces/me");
  if (me.ok) return;
  const created = await fetch("/api/workspaces", { method: "POST" });
  if (!created.ok) throw new Error(`workspace creation failed: ${created.status}`);
}

export function CreateTrip() {
  const [trip, setTrip] = useState<CreatedTrip | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const form = new FormData(event.currentTarget);
    try {
      await ensureWorkspace();
      const response = await fetch("/api/trips", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: form.get("name"),
          starts_on: form.get("starts_on"),
          days: Number(form.get("days")),
        }),
      });
      if (!response.ok) throw new Error(`trip creation failed: ${response.status}`);
      setTrip((await response.json()) as CreatedTrip);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <main>
      <h1>New trip</h1>
      <form onSubmit={submit}>
        <label>
          Name <input name="name" required maxLength={200} />
        </label>
        <label>
          Starts on <input name="starts_on" type="date" required />
        </label>
        <label>
          Days <input name="days" type="number" min={1} max={10} defaultValue={1} required />
        </label>
        <button type="submit">Create trip</button>
      </form>
      {error && <p role="alert">{error}</p>}
      {trip && (
        <p>
          Guest link for {trip.name}: <a href={`/g/${trip.guest_token}`}>/g/{trip.guest_token}</a>
        </p>
      )}
    </main>
  );
}
