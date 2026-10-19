"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { describe, PayPalCheckout } from "./PayPalCheckout";
import type { Purpose } from "./types";

const LABEL: Record<Purpose, string> = {
  deposit: "Pay the deposit",
  balance: "Hold the balance (authorized now, charged only per the policy after the trip)",
};

export function GuestPayments({ token, purpose }: { token: string; purpose: Purpose }) {
  const router = useRouter();
  const [clientId, setClientId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ client_id: string | null }>("/api/paypal/config")
      .then((config) => setClientId(config.client_id))
      .catch(() => setClientId(null));
  }, []);

  async function payWithTestCard() {
    setBusy(true);
    setMessage(null);
    try {
      await api(`/api/guest/${encodeURIComponent(token)}/${purpose}/test-card`, { body: {} });
      router.refresh();
    } catch (error) {
      setMessage(describe(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>{LABEL[purpose]}</h2>
      {clientId && (
        <PayPalCheckout
          clientId={clientId}
          token={token}
          purpose={purpose}
          onDone={() => router.refresh()}
          onError={setMessage}
        />
      )}
      <button type="button" disabled={busy} onClick={() => void payWithTestCard()}>
        Use the sandbox test card
      </button>
      {message && <p role="alert">{message}</p>}
    </section>
  );
}
