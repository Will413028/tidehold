import "server-only";
import { backendUrl } from "@/lib/config";
import type { GuestView } from "./types";

export async function fetchGuestView(token: string): Promise<GuestView | null> {
  const response = await fetch(`${backendUrl}/api/guest/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`guest lookup failed: ${response.status}`);
  return (await response.json()) as GuestView;
}
