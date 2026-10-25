"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { ensureWorkspace } from "@/lib/workspace";

type Seeded = { trip_id: string; guests: { name: string; token: string }[] };

const PURPOSES = ["deposit", "balance"] as const;

function reason(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e);
}

/** Loads the fictional demo operator into an empty workspace (plan step 9 unit 8), then pays
 * each guest through the guest page's own test-card route, one PayPal call per request. */
export function DemoOperator() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ tripId: string; what: string[] } | null>(null);

  async function load() {
    setBusy(true);
    setStatus("Creating the demo operator…");
    let seeded: Seeded;
    try {
      await ensureWorkspace();
      seeded = await api<Seeded>("/api/demo-operator", { body: {} });
    } catch (e) {
      setStatus(reason(e));
      setBusy(false);
      return;
    }
    const what: string[] = [];
    for (const guest of seeded.guests) {
      for (const purpose of PURPOSES) {
        setStatus(`Paying ${guest.name}'s ${purpose} with the PayPal sandbox test card…`);
        try {
          await api(`/api/guest/${guest.token}/${purpose}/test-card`, { body: {} });
        } catch (e) {
          what.push(`${guest.name}'s ${purpose} (${reason(e)})`);
        }
      }
    }
    if (what.length === 0) {
      router.push(`/trips/${seeded.trip_id}`);
      return;
    }
    setFailed({ tripId: seeded.trip_id, what });
    setStatus(null);
  }

  return (
    <section aria-label="Demo operator">
      <h2>Try the demo operator</h2>
      <p>
        Northline Dive Co. is fictional; its cancellation wording quotes Pure Dive Resort's published policy.
        Loading it publishes that policy, creates a two-day trip in Batanes starting today, and has two
        synthetic guests pay their deposit and balance with the PayPal sandbox test card.
      </p>
      <button type="button" disabled={busy} onClick={() => void load()}>
        Load the demo operator
      </button>
      {status && <p role="status">{status}</p>}
      {failed && (
        <p role="alert">
          Not paid: {failed.what.join(", ")}. Each guest's page can pay it.{" "}
          <a href={`/trips/${failed.tripId}`}>Open the trip</a>
        </p>
      )}
    </section>
  );
}
