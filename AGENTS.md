# tidehold

Weather-cancellation deposits for dive operators, built for the PayPal AI Hackathon on Devpost. `backend/` is FastAPI on PostgreSQL 18 (Python 3.13, uv), `web/` is Next.js (TypeScript, pnpm), `recorder/` records official warning feeds (stdlib Python, runs on the VM).

## Product contract (decided)

- Primary user: a weather-dependent dive operator anywhere that takes trip payments from guests. The guest sees one transparency page; there is no guest product of its own.
- The agent compiles the operator's own cancellation wording into structured rules. Every rule carries a verbatim span from that wording; a clause that is silent or left to the operator's discretion becomes "needs operator ruling", never a guess.
- The booking deposit is captured through PayPal at booking; on a weather cancellation it is refunded or recorded as credit, per the compiled policy.
- The trip balance is held as a PayPal Orders v2 AUTHORIZE at T-(29 − N) for an N-day trip, so every capture falls inside PayPal's 29-day validity; a capture after the 3-day honor period is preceded by a reauthorization; trips longer than 10 days are refused. See `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`.
- Triggers are official warnings in CAP (Common Alerting Protocol) from the national weather service the operator names in its policy (e.g. PAGASA, NWS, MeteoAlarm, JMA, BoM, CWA). Open-Meteo marine forecasts only corroborate; Open-Meteo states it is not for coastal navigation.
- The model never supplies amounts. Code computes every amount from the compiled rules. Inside the operator's mandate the agent voids or partially captures holds; outside it, or on any unruled clause, it stops and asks the operator.
- Credit (as opposed to refund) is an internal ledger record; PayPal has no native voucher.
- PayPal sandbox and synthetic guests only; no real money. Whether a boat goes out is always the operator's call; this is not a dive-safety tool.
- Real operators' clauses may be quoted with their source URL and access date; the demo operator is a neutral fictional name with no real operator's branding.
- Recorded official feeds used in a demo are labelled as recorded, with their fetch time; the agent sees only data timestamped before each decision point.

## Reuse

The money-command engine (stored-before-sent PayPal commands with PayPal-Request-Id, reconciliation, mandate check, server-verified citations) comes from `Will413028/dive-trip-agent` at commit `8065eba` (branch `feat/paypal-deposits`): `backend/src/dive_trip/modules/deposits/` and its dependencies, as listed in the plan's step 4. Re-evaluate each mechanism when porting instead of copying it wholesale. Time-based work runs on a PostgreSQL `due_actions` table, not a workflow engine (`docs/adr/2026-10-19-runtime-without-workflow-engine.md`).

## Layers

Backend (`backend/src/tidehold/`), top to bottom; a module imports only modules below it, and modules on one line are independent of each other:

1. `bootstrap`: FastAPI app and wiring
2. `settlement`
3. `holds`
4. `trips` | `warnings`
5. `policy` | `money` | `identity`
6. `platform`: settings, database, migrations

`warnings` does not import `trips`; the caller passes the warning area in. A new top-level module must be added to the `[tool.importlinter]` layers in `backend/pyproject.toml` (the contract is exhaustive). When a check fails, move the code; do not add an `ignore_imports` exception.

Web (`web/src/`): `app` → `features` → `lib`. A feature does not import another feature (shared code goes to `lib`); `app` imports a feature only through its `index.ts`; server-only modules import `server-only`. Enforced by `web/.dependency-cruiser.cjs`.

## Checks

- `make check`: lint, typecheck, layer checks, unit and integration tests (integration starts a PostgreSQL container; needs Docker). `make security`: gitleaks, osv-scanner, actionlint, zizmor.
- Design rules beyond layering: ruff (`backend/pyproject.toml`; `recorder/` and `scripts/` have their own `ruff.toml`) adds complexity, swallowed errors, naive datetimes, boolean traps and `TRY`; mypy strict adds exhaustive `match`; Biome adds floating and misused promises and cognitive complexity. Only `backend/src/tidehold/platform/settings.py` and `web/src/lib/config.ts` (plus `next.config.ts`) read the environment (ruff `TID251`, Biome `noProcessEnv`). pytest runs in random order (pytest-randomly).
- `quality-lock.json` locks every kind of suppression comment (`noqa`, `ruff: ignore`, `type: ignore`, `biome-ignore`, `ts-expect-error`, ...) and the executed test count per suite; `make locks` fails on any difference, so lower a suppression count or raise a test count in the change that earns it. A commit that raises a suppression count or lowers a test count needs a `Quality-lock-reason:` line in its message (checked on pre-push and in CI); list the tests removed with a deleted feature there. Fix new violations instead of suppressing them; the 13 `noqa` in `recorder/` are the baseline.
- Commits go straight to `main`. The gate is the lefthook `pre-push` hook, which runs `make check`, `make security` and the lock history check; run `lefthook install` once per clone. CI (`.github/workflows/ci.yml`) runs the same targets as a backup; `osv-weekly.yml` rescans `main` weekly.
- GitHub Actions are pinned by full commit SHA (images by digest) with the version in a comment; workflows default to `permissions: contents: read`.
- Local dev: `make dev-db`, `make dev-backend`, `make dev-web`.

## Dependencies

- Agents do not add or upgrade dependencies on their own: list the package and version, confirm it exists and is not newly registered (check the registry's release history), and wait for approval.
- New releases are taken only after a week: `exclude-newer = "1 week"` in `backend/pyproject.toml`, `minimumReleaseAge: 10080` in `web/pnpm-workspace.yaml`, Dependabot `cooldown` 7 days. Lockfiles are committed; osv-scanner lists them explicitly, so a new lockfile is added to `make security` and both workflows.
- Vulnerability fix deadlines from the advisory's publication: critical 7 days, high 14 days, moderate and low 30 days. Report channel: `SECURITY.md`.
