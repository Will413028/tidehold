"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PolicyVersion } from "@/lib/api-types";
import { formatDateRange } from "@/lib/dates";
import { type Column, Grid } from "@/lib/grid";
import { formatUsd } from "@/lib/money";
import { TripCard } from "./TripCard";
import { TripForm } from "./TripForm";
import type { Trip } from "./types";

const COLUMNS: Column<Trip>[] = [
  {
    headerName: "Trip",
    field: "name",
    cellRenderer: ({ data }: { data?: Trip }) => data && <a href={`/trips/${data.id}`}>{data.name}</a>,
  },
  { headerName: "Dates", valueGetter: ({ data }) => data && formatDateRange(data.starts_on, data.ends_on) },
  { headerName: "Guests", valueGetter: ({ data }) => data?.guests.length },
  { headerName: "Deposit", valueGetter: ({ data }) => data && formatUsd(data.deposit_minor) },
  { headerName: "Balance", valueGetter: ({ data }) => data && formatUsd(data.balance_minor) },
  {
    headerName: "Warnings",
    valueGetter: ({ data }) => data && `${data.warning_source} ${data.warning_areas.join(", ")}`,
  },
];

export function TripsHome() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [versions, setVersions] = useState<PolicyVersion[]>([]);

  useEffect(() => {
    // no workspace yet (401): it is created with the first policy or trip
    api<Trip[]>("/api/trips")
      .then(setTrips)
      .catch(() => setTrips([]));
    api<PolicyVersion[]>("/api/policy-versions")
      .then(setVersions)
      .catch(() => setVersions([]));
  }, []);

  const replace = (trip: Trip) => setTrips((all) => all.map((t) => (t.id === trip.id ? trip : t)));

  return (
    <main>
      <nav>
        <a href="/">Trips</a> · <a href="/policies">Policies</a>
      </nav>
      <h1>Trips</h1>
      <Grid label="Trips" rows={trips} columns={COLUMNS} rowId={(t) => t.id} />
      {trips.map((trip) => (
        <TripCard key={trip.id} trip={trip} onChange={replace} />
      ))}
      <h2>New trip</h2>
      <TripForm versions={versions} onCreated={(trip) => setTrips((all) => [...all, trip])} />
    </main>
  );
}
