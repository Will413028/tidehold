import "server-only";
import { backendUrl } from "@/lib/config";
import type { GuestTripView } from "./types";

export async function fetchGuestTrip(token: string): Promise<GuestTripView | null> {
  const response = await fetch(`${backendUrl}/api/guest/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`guest trip lookup failed: ${response.status}`);
  return (await response.json()) as GuestTripView;
}
