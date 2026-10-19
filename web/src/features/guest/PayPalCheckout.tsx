"use client";

// Ported from dive-trip-agent@8065eba src/features/deposits/PayPalCheckout.tsx: the deposit is a
// CAPTURE order and the balance an AUTHORIZE order (D3, D11), so the SDK is loaded per intent.
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Purpose } from "./types";

// The slice of the PayPal JS SDK this page uses.
type PayPalButtons = {
  render(container: HTMLElement): Promise<void>;
  close(): Promise<void>;
  isEligible(): boolean;
};
type PayPalNamespace = {
  Buttons(options: {
    style?: Record<string, string>;
    createOrder: () => Promise<string>;
    onApprove: (data: { orderID: string }) => Promise<void>;
    onCancel?: () => void;
    onError?: (error: unknown) => void;
  }): PayPalButtons;
};

type Intent = "capture" | "authorize";
const INTENT: Record<Purpose, Intent> = { deposit: "capture", balance: "authorize" };
const sdks = new Map<Intent, Promise<PayPalNamespace>>();

function loadSdk(clientId: string, intent: Intent): Promise<PayPalNamespace> {
  const cached = sdks.get(intent);
  if (cached !== undefined) return cached;
  const loading = new Promise<PayPalNamespace>((resolve, reject) => {
    const params = new URLSearchParams({
      "client-id": clientId,
      currency: "USD",
      intent,
      components: "buttons",
      "enable-funding": "card",
      "disable-funding": "paylater,venmo",
    });
    const script = document.createElement("script");
    script.src = `https://www.paypal.com/sdk/js?${params}`;
    script.async = true;
    // Each intent gets its own namespace, so a page that loaded the other one keeps working.
    script.dataset.namespace = `paypal_${intent}`;
    script.onload = () => {
      const sdk = (window as unknown as Record<string, PayPalNamespace | undefined>)[`paypal_${intent}`];
      if (sdk) resolve(sdk);
      else reject(new Error("PAYPAL_SDK_MISSING"));
    };
    script.onerror = () => {
      sdks.delete(intent);
      reject(new Error("PAYPAL_SDK_UNAVAILABLE"));
    };
    document.head.append(script);
  });
  sdks.set(intent, loading);
  return loading;
}

export function describe(error: unknown): string {
  if (error instanceof ApiError && error.status === 501) {
    return "Payments are not set up on this server (no PayPal sandbox credentials).";
  }
  if (error instanceof ApiError && error.status === 503) {
    return "PayPal did not confirm the payment yet. Refresh in a minute, or use the sandbox test card.";
  }
  if (error instanceof ApiError) return `The payment was refused (${error.detail}).`;
  return "PayPal checkout failed. You can retry, or use the sandbox test card.";
}

export function PayPalCheckout({
  clientId,
  token,
  purpose,
  onDone,
  onError,
}: {
  clientId: string;
  token: string;
  purpose: Purpose;
  onDone: () => void;
  onError: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const latest = useRef({ onDone, onError });
  latest.current = { onDone, onError };
  const [ready, setReady] = useState<"loading" | "ready" | "unavailable">("loading");

  useEffect(() => {
    let buttons: PayPalButtons | null = null;
    let cancelled = false;
    // PayPal may hand onError its own error instead of ours, so keep what the server said.
    let refused: unknown = null;
    const base = `/api/guest/${encodeURIComponent(token)}/${purpose}/checkout`;
    loadSdk(clientId, INTENT[purpose])
      .then(async (paypal) => {
        if (cancelled || !container.current) return;
        buttons = paypal.Buttons({
          style: { layout: "vertical", label: "pay" },
          createOrder: async () => {
            refused = null;
            try {
              return (await api<{ order_id: string }>(base, { body: {} })).order_id;
            } catch (error) {
              refused = error;
              throw error;
            }
          },
          onApprove: async ({ orderID }) => {
            try {
              await api(`${base}/${encodeURIComponent(orderID)}`, { body: {} });
              latest.current.onDone();
            } catch (error) {
              latest.current.onError(describe(error));
            }
          },
          onCancel: () => latest.current.onError("Checkout cancelled; nothing was charged or held."),
          onError: (error) => latest.current.onError(describe(refused ?? error)),
        });
        if (!buttons.isEligible()) {
          setReady("unavailable");
          return;
        }
        await buttons.render(container.current);
        if (!cancelled) setReady("ready");
      })
      .catch(() => {
        if (!cancelled) setReady("unavailable");
      });
    return () => {
      cancelled = true;
      void buttons?.close().catch(() => undefined);
    };
  }, [clientId, token, purpose]);

  return (
    <div>
      {ready === "loading" && <p role="status">Loading PayPal checkout…</p>}
      {ready === "unavailable" && (
        <p role="alert">PayPal checkout is unavailable right now; use the sandbox test card.</p>
      )}
      <div ref={container} />
    </div>
  );
}
