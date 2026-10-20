"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { formatDateRange } from "@/lib/dates";
import { formatUsd } from "@/lib/money";
import { OperatorNav } from "@/lib/nav";
import { ActivitySection } from "./ActivitySection";
import { CancelForm } from "./CancelForm";
import type { Evidence, FailedAction, Proposal, TimelineEvent } from "./console-types";
import { EvidenceSection } from "./EvidenceSection";
import { GuestsSection } from "./GuestsSection";
import { ProposalsSection } from "./ProposalsSection";
import type { Trip } from "./types";

export function TripPage({ id }: { id: string }) {
  const [trip, setTrip] = useState<Trip | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [failed, setFailed] = useState<FailedAction[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [missing, setMissing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [t, p, e, f, l] = await Promise.all([
        api<Trip>(`/api/trips/${id}`),
        api<Proposal[]>(`/api/trips/${id}/proposals`),
        api<Evidence>(`/api/trips/${id}/evidence`),
        api<FailedAction[]>(`/api/trips/${id}/failed-actions`),
        api<TimelineEvent[]>(`/api/trips/${id}/timeline`),
      ]);
      setTrip(t);
      setProposals(p);
      setEvidence(e);
      setFailed(f);
      setTimeline(l);
    } catch {
      setMissing(true); // another workspace's trip, or no workspace: 404 or 401
    }
  }, [id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const names = useMemo(() => new Map(trip?.guests.map((g) => [g.id, g.name])), [trip]);

  if (missing) return <main>Trip not found in this browser's workspace.</main>;
  if (trip === null) return <main>Loading…</main>;
  return (
    <main>
      <OperatorNav />
      <h1>{trip.name}</h1>
      <p>
        {formatDateRange(trip.starts_on, trip.ends_on)} ({trip.timezone}) · deposit{" "}
        {formatUsd(trip.deposit_minor)} · balance {formatUsd(trip.balance_minor)} · mandate{" "}
        {formatUsd(trip.mandate_guest_minor)} per guest, {formatUsd(trip.mandate_trip_minor)} per trip ·
        warnings: {trip.warning_source} {trip.warning_areas.join(", ")}
      </p>
      <GuestsSection trip={trip} onChange={setTrip} />
      <EvidenceSection
        trip={trip}
        evidence={evidence}
        onReplayed={(e) => {
          setEvidence(e);
          void refresh();
        }}
      />
      <CancelForm trip={trip} onDone={() => void refresh()} />
      <ProposalsSection
        tripId={trip.id}
        proposals={proposals}
        names={names}
        onDecided={() => void refresh()}
      />
      <ActivitySection failed={failed} timeline={timeline} />
    </main>
  );
}
