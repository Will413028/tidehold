# tidehold

Weather-cancellation deposits for dive operators, built for the PayPal AI Hackathon on Devpost. `backend/` is FastAPI on PostgreSQL 18 (Python 3.13, uv), `web/` is Next.js (TypeScript, pnpm), `recorder/` records official warning feeds (stdlib Python, runs on the VM).

## Product contract (decided)

- Primary user: a weather-dependent dive operator anywhere that takes trip payments from guests. The guest sees one transparency page; there is no guest product of its own.
- The agent compiles the operator's own cancellation wording into structured rules. Every rule carries a verbatim span from that wording; a clause that is silent or left to the operator's discretion becomes "needs operator ruling", never a guess. The model compiles (rules, needs-ruling items, proposed event sets) and drafts operator asks and guest explanations, with a template fallback; settlement makes no model call. See `docs/adr/2026-10-19-settlement-triggers-and-model-role.md`.
- The booking deposit is captured through PayPal at booking; on a weather cancellation it is refunded or recorded as credit, per the compiled policy.
- The trip balance is paid by the guest and captured before the trip: due 30 days before the first trip day by default (operator-adjustable), at once for later bookings; an unpaid balance at its due date becomes an operator ask. Nothing is held as an authorization. Each accepted settlement executes when decided, as a refund of the captured deposit or balance, as a reduction of what is still unpaid on it, or as credit (written once the payment is captured); a payment may be refunded several times, never above what it captured. An unpaid balance's ask is answered by waiting or by releasing the guest, who then owes nothing more. See `docs/adr/2026-10-23-balance-paid-before-trip-weather-refunds.md`.
- A settlement starts only from the operator's recorded cancellation (a trip day, or the rest of the trip from a day, with its cause). A rule whose wording conditions on an official warning also requires a warning in CAP (Common Alerting Protocol) from the national weather service the operator names (e.g. PAGASA, NWS, MeteoAlarm), matched in code against the event set the operator confirmed; a matched warning without a cancellation becomes an operator ask, never money movement. Open-Meteo marine forecasts only corroborate; Open-Meteo states it is not for coastal navigation.
- The model never supplies amounts. Code computes every amount from the compiled rules. Inside the operator's mandate the system refunds or credits; outside it, or on any unruled clause, it stops and asks the operator. Deductions (for example a processing fee) come only from the operator's wording with their own verified span; without one the guest gets the full amount and the operator absorbs PayPal's unreturned fee.
- Credit (as opposed to refund) is an internal ledger record; PayPal has no native voucher.
- PayPal sandbox and synthetic guests only; no real money. Whether a boat goes out is always the operator's call; this is not a dive-safety tool.
- Real operators' clauses may be quoted with their source URL and access date; the demo operator is a neutral fictional name with no real operator's branding.
- Recorded official feeds used in a demo are labelled as recorded, with their fetch time; a decision sees only data timestamped before its decision point.

## Reuse

The money engine was ported in plan step 4 from `Will413028/dive-trip-agent` at commit `8065eba` (branch `feat/paypal-deposits`): `money/{paypal,commands,service}.py` from `backend/src/dive_trip/modules/deposits/{paypal,transactions,service}.py`, `settlement/plan.py` from `domain.py` (`settle_diver`), `bootstrap/http_boundary.py` from the same path. The mandate check (`domain.py`, `check_mandate`) was re-evaluated in step 8a: only its caps remain, per guest and per trip on the trip, summed over the trip's accepted settlements (`settlement/evaluate.py`; `docs/plans/2026-10-19-tidehold-hackathon/step8a-design.md`). The runtime assessor (`assessor.py`) is not ported: evidence is structured CAP and cancellation is a recorded operator action (`docs/adr/2026-10-19-settlement-triggers-and-model-role.md`). Time-based work runs on a PostgreSQL `due_actions` table, not a workflow engine (`docs/adr/2026-10-19-runtime-without-workflow-engine.md`).

## Layers

Backend (`backend/src/tidehold/`), top to bottom; a module imports only modules below it, and modules on one line are independent of each other:

1. `bootstrap`: FastAPI app and wiring
2. `settlement`
3. `holds`
4. `trips`
5. `policy`
6. `warnings` | `money` | `identity`
7. `platform`: settings, database, migrations

`warnings` does not import `trips`; the caller passes the warning area in. `policy` sits above `warnings` because a rule's warning condition names values from its event catalogue. A new top-level module must be added to the `[tool.importlinter]` layers in `backend/pyproject.toml` (the contract is exhaustive). When a check fails, move the code; do not add an `ignore_imports` exception.

Web (`web/src/`): `app` → `features` → `lib`. A feature does not import another feature (shared code goes to `lib`); `app` imports a feature only through its `index.ts`; server-only modules import `server-only`. Enforced by `web/.dependency-cruiser.cjs`.

## Checks

- `make check`: lint, typecheck, layer checks, unit and integration tests; integration starts a PostgreSQL container (needs Docker). `make ci` adds the browser tests (`make e2e`, Playwright on desktop and mobile widths against `backend/tests/e2e/server.py`, which fakes PayPal, the model and Open-Meteo); only CI runs it, never the developer's machine or an agent on it. `make security`: gitleaks, osv-scanner, actionlint, zizmor.
- Design rules beyond layering: ruff (`backend/pyproject.toml`; `recorder/` and `scripts/` have their own `ruff.toml`) adds complexity, swallowed errors, naive datetimes, boolean traps and `TRY`; mypy strict adds exhaustive `match`; Biome adds floating and misused promises and cognitive complexity. Only `backend/src/tidehold/platform/settings.py` and `web/src/lib/config.ts` (plus `next.config.ts`) read the environment (ruff `TID251`, Biome `noProcessEnv`). pytest runs in random order (pytest-randomly).
- `quality-lock.json` locks every kind of suppression comment (`noqa`, `ruff: ignore`, `type: ignore`, `biome-ignore`, `ts-expect-error`, ...) and the executed test count per suite; `make locks` fails on any difference, so lower a suppression count or raise a test count in the change that earns it. A commit that raises a suppression count or lowers a test count needs a `Quality-lock-reason:` line in its message (checked on pre-push and in CI); list the tests removed with a deleted feature there. Fix new violations instead of suppressing them; the 13 `noqa` in `recorder/` are the baseline.
- Commits go straight to `main`. The gate is the lefthook `pre-push` hook, which runs `make check`, `make security` and the lock history check; run `lefthook install` once per clone. CI (`.github/workflows/ci.yml`) runs `make ci` as a backup, and is the only place they run; a push that breaks them shows up there. `.github/workflows/security.yml` calls fullstack-template's security workflow at a pinned commit (actionlint + zizmor, gitleaks, osv-scanner over the lockfiles it names) on every push and rescans `main` weekly.
- GitHub Actions are pinned by full commit SHA (images by digest) with the version in a comment; workflows default to `permissions: contents: read`.
- `make coverage`: line and branch coverage of `money`, `holds` and `settlement`, report only (no threshold); read it at each step's acceptance.
- `docs/threat-model.md` lists every security control with the test that proves it; change it in the same commit as the surface it describes.
- Local dev: `make dev-db`, `make dev-backend`, `make dev-web`.

## Dependencies

- Agents do not add or upgrade dependencies on their own: list the package and version, confirm it exists and is not newly registered (check the registry's release history), and wait for approval.
- New releases are taken only after a week: `exclude-newer = "1 week"` in `backend/pyproject.toml`, `minimumReleaseAge: 10080` in `web/pnpm-workspace.yaml`, Dependabot `cooldown` 7 days. Lockfiles are committed; osv-scanner lists them explicitly, so a new lockfile is added to `make security` and to `osv-scan-args` in `security.yml`.
- Vulnerability fix deadlines from the advisory's publication: critical 7 days, high 14 days, moderate and low 30 days. A security fix does not wait out the release week: add the package to `exclude-newer-package` in `backend/pyproject.toml` (or `minimumReleaseAgeExclude` in `web/pnpm-workspace.yaml`) with the advisory id, and remove the entry once the release is a week old. Report channel: `SECURITY.md`.
