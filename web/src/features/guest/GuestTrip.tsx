import { formatDateRange } from "@/lib/dates";
import type { GuestTripView } from "./types";

export function GuestTrip({ trip }: { trip: GuestTripView }) {
  return (
    <main>
      <h1>{trip.name}</h1>
      <p>
        {formatDateRange(trip.starts_on, trip.ends_on)} · {trip.days} {trip.days === 1 ? "day" : "days"}
      </p>
    </main>
  );
}
