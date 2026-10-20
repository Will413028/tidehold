"use client";

import { ClientSideRowModelModule, type ColDef, themeQuartz } from "ag-grid-community";
import { AgGridProvider, AgGridReact } from "ag-grid-react";

/** The one place AG Grid is imported (D10): features build columns and pass rows. */
export type Column<T> = ColDef<T>;

const MODULES = [ClientSideRowModelModule];
const DEFAULTS: ColDef = { flex: 1, minWidth: 110, sortable: false, resizable: false };

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
  return (
    <AgGridProvider modules={MODULES}>
      <section aria-label={label}>
        <AgGridReact<T>
          theme={themeQuartz}
          rowData={rows}
          columnDefs={columns}
          defaultColDef={DEFAULTS}
          getRowId={(params) => rowId(params.data)}
          // tables here hold a few rows; the page scrolls, not the grid
          domLayout="autoHeight"
        />
      </section>
    </AgGridProvider>
  );
}
