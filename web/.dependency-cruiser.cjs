/** Direction: app → features → lib. Features do not import each other; lib imports neither. */
module.exports = {
  forbidden: [
    { name: "no-circular", severity: "error", from: {}, to: { circular: true } },
    {
      name: "lib-is-the-bottom",
      severity: "error",
      from: { path: "^src/lib/" },
      to: { path: "^src/(app|features)/" },
    },
    {
      name: "features-do-not-import-app",
      severity: "error",
      from: { path: "^src/features/" },
      to: { path: "^src/app/" },
    },
    {
      name: "features-are-independent",
      comment: "Move shared code to src/lib instead of importing another feature.",
      severity: "error",
      from: { path: "^src/features/([^/]+)/" },
      to: { path: "^src/features/", pathNot: "^src/features/$1/" },
    },
    {
      name: "app-uses-feature-entry-points",
      comment: "Import a feature through its index.ts.",
      severity: "error",
      from: { path: "^src/app/" },
      to: { path: "^src/features/[^/]+/(?!index\\.ts$)" },
    },
    {
      name: "grid-through-lib",
      comment: "AG Grid is imported only by src/lib/grid.tsx (D10); features pass rows and columns.",
      severity: "error",
      from: { path: "^src/", pathNot: "^src/lib/grid\\.tsx$" },
      to: { path: "node_modules/ag-grid-" },
    },
    {
      name: "not-to-unresolvable",
      severity: "error",
      from: {},
      to: { couldNotResolve: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
    exclude: { path: "\\.test\\.tsx?$" },
  },
};
