import { notFound } from "next/navigation";
import { fetchGuestTrip, GuestTrip } from "@/features/guest";

export default async function GuestPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const trip = await fetchGuestTrip(token);
  if (trip === null) notFound();
  return <GuestTrip trip={trip} />;
}
