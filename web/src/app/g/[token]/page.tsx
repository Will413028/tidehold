import { notFound } from "next/navigation";
import { fetchGuestView, fetchSettlementView, GuestTrip } from "@/features/guest";

export default async function GuestPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const view = await fetchGuestView(token);
  if (view === null) notFound();
  return <GuestTrip token={token} view={view} settlement={await fetchSettlementView(token)} />;
}
