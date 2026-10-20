"use client";

import { ClientSideRowModelModule, type ColDef, themeQuartz } from "ag-grid-community";
import { AgGridProvider, AgGridReact } from "ag-grid-react";
import { useEffect, useMemo, useState } from "react";

/** The one place AG Grid is imported (D10): features build columns and pass rows. A `wide`
 * column is left out on narrow screens, so a grid never scrolls sideways on a phone. */
export type Column<T> = ColDef<T> & { wide?: boolean };

const MODULES = [ClientSideRowModelModule];
const DEFAULTS: ColDef = { flex: 1, minWidth: 80, sortable: false, resizable: false };
const NARROW = "(max-width: 720px)";
// The container's height comes from the row count, not domLayout="autoHeight": on a phone,
// autoHeight left rows added after the first render drawn over the content below (e2e, step 9).
const ROW = 42;
const HEADER = 48;
const MAX_ROWS = 12;

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(NARROW);
    setNarrow(query.matches);
    const change = (e: MediaQueryListEvent) => setNarrow(e.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return narrow;
}

export function Grid<T>({
  label,
  rows,
  columns,
  rowId,
}: {
  label: string;
  rows: T[];
  columns: Column<T>[];
  rowId: (row: T) => string;
}) {
  const narrow = useNarrow();
  const shown = useMemo(
    () => columns.filter((c) => !(narrow && c.wide)).map(({ wide: _, ...column }) => column),
    [columns, narrow],
  );
  return (
    <AgGridProvider modules={MODULES}>
      <section
        aria-label={label}
        style={{ height: HEADER + ROW * Math.min(Math.max(rows.length, 1), MAX_ROWS) + 4 }}
      >
        <AgGridReact<T>
          theme={themeQuartz}
          rowData={rows}
          columnDefs={shown}
          defaultColDef={DEFAULTS}
          getRowId={(params) => rowId(params.data)}
          rowHeight={ROW}
          headerHeight={HEADER}
        />
      </section>
    </AgGridProvider>
  );
}
