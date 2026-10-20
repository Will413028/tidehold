"use client";

import { useState } from "react";
import { type Column, Grid } from "@/lib/grid";
import type { Proposal } from "./console-types";
import { DecisionPanel } from "./DecisionPanel";
import { askTitle } from "./proposal-view";

const COLUMNS: Column<Proposal>[] = [
  { headerName: "Ask or settlement", valueGetter: ({ data }) => data && askTitle(data), minWidth: 160 },
  { headerName: "State", field: "state" },
  { headerName: "Decided by", valueGetter: ({ data }) => data?.decided_by ?? "", wide: true },
];

export function ProposalsSection({
  tripId,
  proposals,
  names,
  onDecided,
}: {
  tripId: string;
  proposals: Proposal[];
  names: Map<string, string>;
  onDecided: () => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const waiting = proposals.filter((p) => p.state === "asked");
  const selected = proposals.find((p) => p.id === open && p.state === "asked");

  return (
    <section>
      <h2>Asks and settlements</h2>
      {/* the actions sit outside the grid, which stays an overview on every width */}
      <ul aria-label="Waiting for your decision">
        {waiting.map((p) => (
          <li key={p.id}>
            {askTitle(p)}{" "}
            <button type="button" onClick={() => setOpen(p.id)}>
              Decide
            </button>
          </li>
        ))}
      </ul>
      <Grid label="Asks and settlements" rows={proposals} columns={COLUMNS} rowId={(p) => p.id} />
      {selected && (
        <DecisionPanel
          key={selected.id}
          tripId={tripId}
          proposal={selected}
          names={names}
          onDecided={() => {
            setOpen(null);
            onDecided();
          }}
        />
      )}
    </section>
  );
}
