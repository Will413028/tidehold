import "server-only";
import type { GuestTripView } from "./types";

const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export async function fetchGuestTrip(token: string): Promise<GuestTripView | null> {
  const response = await fetch(`${backend}/api/guest/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`guest trip lookup failed: ${response.status}`);
  return (await response.json()) as GuestTripView;
}
