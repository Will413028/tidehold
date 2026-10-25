"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PolicyVersion } from "@/lib/api-types";
import { formatDateRange } from "@/lib/dates";
import { type Column, Grid } from "@/lib/grid";
import { formatUsd } from "@/lib/money";
import { OperatorNav } from "@/lib/nav";
import { DemoOperator } from "./DemoOperator";
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
  { headerName: "Deposit", valueGetter: ({ data }) => data && formatUsd(data.deposit_minor), wide: true },
  { headerName: "Balance", valueGetter: ({ data }) => data && formatUsd(data.balance_minor), wide: true },
  {
    headerName: "Warnings",
    valueGetter: ({ data }) => data && `${data.warning_source} ${data.warning_areas.join(", ")}`,
    wide: true,
  },
];

export function TripsHome() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [versions, setVersions] = useState<PolicyVersion[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // no workspace yet (401): it is created with the first policy or trip
    void Promise.all([
      api<Trip[]>("/api/trips")
        .then(setTrips)
        .catch(() => setTrips([])),
      api<PolicyVersion[]>("/api/policy-versions")
        .then(setVersions)
        .catch(() => setVersions([])),
    ]).finally(() => setLoaded(true));
  }, []);

  return (
    <main>
      <OperatorNav />
      <h1>Trips</h1>
      {/* the backend seeds only a workspace with neither */}
      {loaded && trips.length === 0 && versions.length === 0 && <DemoOperator />}
      <Grid label="Trips" rows={trips} columns={COLUMNS} rowId={(t) => t.id} />
      <h2>New trip</h2>
      <TripForm versions={versions} onCreated={(trip) => setTrips((all) => [...all, trip])} />
    </main>
  );
}
