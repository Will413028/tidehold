import { TripPage } from "@/features/trips";

export default async function TripDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TripPage id={id} />;
}
