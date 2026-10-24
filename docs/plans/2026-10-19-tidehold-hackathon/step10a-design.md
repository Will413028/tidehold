# Step 10a design: deploy code, built and tested locally

Premise: nothing here is scheduled to be rewritten; the deploy files are new (`git ls-files | rg -i 'compose|docker|deploy'` at 56fd600 finds only `recorder/deploy/`). The money tables are final after step 8c (0017). Nothing runs on the VM in 10a (D23); 10b runs it.

The plan's step 10a Scope and Acceptance are the work list. This file records the shapes and where the build reads the plan text a particular way, and why.

## 1. Nightly reconciliation (`money/audit.py`, `bootstrap/nightly_audit.py`)

What it reads: every row of `money_commands`, with its payment. The plan says "every money command tidehold recorded that day"; the job runs right before the nightly rebuild empties the database, so the database holds exactly what was recorded since the last rebuild. Filtering on `created_at` would add a window to get wrong (a failed rebuild would leave two days, and a 24-hour filter would skip the older one) for no gain: re-reading an old command only reads.

How it reads PayPal: one `GET /v2/checkout/orders/{id}` per distinct order (an order shows its capture and every refund with its `invoice_id`, which `money/service.py` `_read_refund` already relies on). No new PayPal call.

Verdict per command (`match`, `mismatch`, `open`, `unread`):

| Command | Local status | PayPal shows | Verdict |
|---|---|---|---|
| open | succeeded | a COMPLETED capture with the payment's `capture_id` and the command's amount | match; anything else mismatch |
| open | rejected | no capture on the command's order (or no order id: never reached PayPal) | match; a capture is mismatch |
| refund | succeeded | a refund with the command's `refundId` or `invoice_id`, COMPLETED, same amount | match; missing, other status or amount is mismatch (the seeded case) |
| refund | rejected | no COMPLETED refund with its `invoice_id` | match; a COMPLETED one is mismatch |
| open | rejected, sent, no order id | nothing to read (PayPal refused the order's creation) | not_readable |
| any | pending / unknown | whatever the order shows | open: listed with PayPal's state. The worker is stopped for the audit and the rebuild drops the command, so nothing settles it afterwards; it counts as a failure (exit 1) |
| any | — | the order read failed | unread (exit 1) |

Per payment, also: `captured_minor` equals the COMPLETED capture's amount, `refunded_minor` equals the sum of COMPLETED refunds, and every refund PayPal lists has a command (a refund at PayPal with no command is a mismatch). These catch money PayPal moved that no command explains.

Read-only toward PayPal by construction, not by convention (the database is dropped minutes later, so a database guard would protect nothing; the audit selects its rows first, then reads PayPal outside any transaction, as `platform/db.py` requires):
- the CLI's `PayPalClient` gets a transport that refuses every request except `GET` and the OAuth token `POST /v1/oauth2/token` (`READ_ONLY_REFUSED`); the integration test runs the audit on `FakePayPal` and asserts `posts("") == 0` beyond the token.

The evidence log: the database is dropped right after the audit, so the log lives outside it, as JSON lines in `RECONCILE_LOG_DIR/reconcile-<UTC date>.jsonl` (one line per command, one per payment check, one summary line), on the named volume `reconcile-log`, which the rebuild does not remove. The summary line is also printed to stdout (the systemd journal). Rows carry ids, amounts and statuses only; no names, tokens or PayPal payer details. Exit code 1 when any row is `mismatch`, `open` or `unread`, so the timer shows a failure. The fallback's script rebuilds after a failed void because a hold it leaves expires on its own; a failed audit is different: the rebuild would delete what explains it. So on a non-zero audit the script first writes `pg_dump --data-only -t payments -t money_commands -t payment_events` (ids, amounts, statuses, PayPal answers; no guest names) next to the log, then rebuilds (the demo must come back each morning) and exits with the audit's status. Retention: the log and dumps stay on the VM until the stack is torn down (trigger in Scope's deferred list: hackathon results announced); listed in the threat model's data inventory.

## 2. Workspace-creation cap per address (D15)

- Table `workspace_creations (address_key text, created_at timestamptz)` (migration 0018), wiped by the nightly rebuild. Necessity: the cap needs to count earlier creations from the same address, and the count must survive an API restart (`restart: unless-stopped` brings a crashed API back with an empty in-memory counter), as the existing caps' counts do; `workspaces` has no address and should not get one.
- `address_key`: the client address, IPv4 as is, IPv6 cut to its /64 (one host usually controls a whole /64); an unparsable address is the key `unknown` (one shared bucket, so it cannot bypass the cap).
- Which address: `request.client.host`. uvicorn's `ProxyHeadersMiddleware` is on by default and trusts `X-Forwarded-For` only from `127.0.0.1` (`forwarded_allow_ips`), taking the rightmost untrusted entry; web reaches the API over loopback, so the client host is the forwarded address with no setting of ours (design review finding 1). Why the forwarded value is the real client (read from source, not measured yet): Tailscale serve *sets* (not appends) `X-Forwarded-For` to the connection's source, which for a Funnel flow is the original client (`ipn/ipnlocal/serve.go` `addProxyForwardedHeaders`, `HandleIngressTCPConn`), so a client-sent value is overwritten; Next 16.4.2 keeps an existing `X-Forwarded-For` (`base-server.js` `??=`) and its rewrite proxy (httpxy, no `xfwd`) forwards headers unchanged; the API listens only on loopback in a namespace shared with web. Unit 3 measures the Next and uvicorn part with `next start` in front of the real API; the Tailscale part is measured in 10b. If 10b finds every Funnel request carries one address, the per-address cap would become one bucket for all judges: 10b then sets `WORKSPACE_DAILY_CAP_PER_ADDRESS` to a global figure (200) and records it in the plan.
- Under `pg_advisory_xact_lock(hashtextextended(address_key, 0))`, count rows since the start of the UTC day (the window of the existing PayPal cap, `money/commands.py` `_within_daily_cap`); at `WORKSPACE_DAILY_CAP_PER_ADDRESS` (default 20: judges behind one office address each create one, and leaving creates a new one) refuse with `LimitReached("WORKSPACE_CREATION_CAP_REACHED")` → 429; else insert the row with the workspace.
- `docs/threat-model.md`: the surface row for workspace creation names this test; the data inventory gains the address key (kept one day).

## 3. Compose project (`deploy/`)

Modelled on the fallback's `deploy/deposits-demo/` (dive-trip-agent), re-evaluated per service:

| Service | Kept from the fallback / changed |
|---|---|
| `db` | PostgreSQL 18, own `db_password` |
| `migrate` | one-off, `python -m tidehold.platform.migrate` |
| `ingest` | one-off after `migrate`: `python -m tidehold.bootstrap.ingest_warnings /recordings`, the D14 directory `/var/lib/tidehold-recorder` mounted read-only. The plan says "by the worker"; the worker runs inside the API process (`RUN_WORKER`, runtime ADR), and the recordings only need to be read once per rebuild: the demo replays recorded warnings (D12). Necessity condition for an in-day ingest loop: the judge path uses a warning fetched after that night's rebuild; it does not. |
| `net` | the shared network namespace (loopback API), kept |
| `api` | uvicorn on `127.0.0.1:8000`, worker in process; the only service with the model keys |
| `web` | Next production server in `net`'s namespace; `BACKEND_URL=http://127.0.0.1:8000` at build time (rewrites are compiled into the routes manifest) |
| `ts` | dedicated Tailscale node `tidehold`, Funnel to `http://net:3000`, state volume kept across rebuilds; its own tag `tag:tidehold-demo` (D5 isolates the stack; a shared tag would widen both nodes together). The tailnet policy gains the tag's owner and its Funnel node attribute in 10b, beside the login approval the developer gives there anyway |
| `audit` | maintenance profile, the nightly reconciliation; `reconcile-log` volume |
| Temporal, `void-holds` | dropped: `due_actions` (runtime ADR), no holds (D18) |

- Secrets by path (D13): `${SHARED_SECRETS_DIR}/paypal.env` (the fallback's sandbox app) and `models.env`; `${TIDEHOLD_SECRETS_DIR}/db_password` and `anthropic.env` (`required: false`; absent = no-AI path). `PAYPAL_ENV: sandbox` is set in compose. Nothing reads or prints them; the database password reaches the app as `DATABASE_PASSWORD_FILE` (read in `platform/settings.py`, the only module that reads the environment).
- Hardening kept: read-only root with `tmpfs: /tmp` (db is writable, the official image needs it), `cap_drop: ALL` (not on db), `no-new-privileges`, `restart: unless-stopped`, log rotation, no host port, and a `cpus` limit per service (the fallback on the same VM must keep its share: Must not change).
- Revisions: images are tagged with the git short SHA (`TIDEHOLD_REVISION`). A new revision is deployed by the same script with `TIDEHOLD_NEXT_REVISION`: the running revision's `audit` reads the database its own schema made, then the database is dropped and the new revision migrates an empty one. Kept from the fallback; the reason there (its migrations refuse existing data) does not hold here, but the audit-matches-schema reason does.
- Images, pinned by digest with the tag in a comment, and Dependabot's `docker` ecosystem (cooldown 7 days) added for `deploy/`: backend on `ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim` (the uv CI pins and the sandbox runs use); web on `node:24.21.0-trixie-slim` (CI runs Node 24); `postgres` 18 alpine; `tailscale/tailscale:v1.104.1` (the fallback's). Approved by the developer 2026-10-24 (AGENTS.md Dependencies), with `postgres:18.6-alpine3.24` taken before its week (pushed 2026-10-22) by the developer's choice over 18.4.
- Memory limits: `db` 384m, `api` 512m, `web` 384m, `net` 32m, `ts` 128m, one-offs 256m (≈1.4 GB with the fallback's ≈2.2 GB). Not measured: the VM did not answer ssh on 2026-10-24; 10b reads `free -m` and `docker stats` before `up` and lowers them if needed.
- Images: `deploy/backend.Dockerfile` (uv, `--frozen --no-dev`, uid 10001, `/var/log/tidehold` owned by it so the new named volume is writable), `deploy/web.Dockerfile`; a root `.dockerignore` allowlist. Built locally once as evidence; 10b builds them at the deployed SHA.
- `deploy/nightly.sh`: stop `web api`, run `audit`, remove app and `db` containers and the `db-data` volume, `up -d`, exit with the audit's status. `ts` and `reconcile-log` are untouched.
- Schedule: `deploy/tidehold-nightly.timer` at 11:00 UTC (not the fallback's 10:00; outside 09:30–10:30 where the fallback rebuilds; 03:00 PST / 19:00 PHT), `Persistent=true` (a run missed while the VM was down runs at boot; it may overlap the fallback's catch-up run, which the CPU limits absorb), `TimeoutStartSec=15min`.
- `deploy/README.md`: host files, build and load, first start, rollback (`docker compose -p tidehold down`, never `--volumes` of another project).

## Tests and acceptance

- `tests/integration/test_audit.py`:
  - every command of the day is listed with PayPal's state: deposit captured, balance captured, one refund completed, one refused (rejected), one pending → five command rows plus payment rows, verdicts as in the table;
  - a seeded mismatch (a refund command set `succeeded` that the fake order does not list) is reported, exit code 1; mutation: filter out `kind = 'refund'` in the audit → this test fails;
  - a refund at PayPal with no command is a mismatch;
  - nothing sent: the fake saw no POST but the token.
- `tests/unit/test_paypal.py`: the read-only transport refuses a POST to `/v2/checkout/orders/x/capture`.
- `tests/integration/test_sessions.py`: the 21st creation from one address in 24 hours is 429 `WORKSPACE_CREATION_CAP_REACHED`; another address still creates; two IPv6 addresses in one /64 share the cap (the test client's address set per request through the ASGI scope).
- `tests/integration/test_deploy.py`: `docker compose -p tidehold -f deploy/compose.yaml config --format json` with placeholder variables validates, and the parsed config has: project `tidehold`, no published port, a memory limit on every service, the recordings mount read-only, PayPal and model keys only by `env_file` path, only `api` (and `audit` for PayPal) given them; the timer's `OnCalendar` is not 10:00.
- `quality-lock.json`: integration and unit counts raised by the tests added.

## Units and order

1. Audit (module, read-only transport, CLI, tests, mutation). Blocks nothing else.
2. Workspace cap (migration 0018, settings, route, tests, threat model). Independent of 1.
3. Deploy files (Dockerfiles, compose, nightly script, timer, README, compose test), local image builds, and the Next `X-Forwarded-For` measurement. After 1 and 2 (it runs their CLIs and sets their settings).

Each unit is its own commit; `make check` before each push; CI green at the end.

## Design review at start (2026-10-24)

A fresh subagent reviewed this file against the fallback's `deploy/deposits-demo/` and the plan; 11 findings plus one low-confidence note. Changed: own `X-Forwarded-For` parsing and `TRUST_FORWARDED_FOR` dropped for uvicorn's proxy-headers middleware (it already trusts loopback only); a failed audit keeps a `pg_dump` of the money tables before the rebuild; `open` at night counts as a failure, since the rebuild drops it unsettled (and `docs/threat-model.md` stops saying the nightly job resolves unknown commands); images pinned by digest with Dependabot's `docker` ecosystem, listed for approval; the log's retention goes into the threat model's data inventory; the database read-only transaction dropped (the database is dropped minutes later; PayPal reads run outside any transaction); own tag `tag:tidehold-demo`; a sent, rejected open with no order id is `not_readable`, not `match`. Recorded: the revision tag and deploy-by-rebuild, the hardening and timer settings, the fallback if Funnel shows one address. Rejected: an in-memory creation counter (a crashed API restarts with an empty counter; the table keeps the count), but its window becomes the UTC day of the existing cap.

## Where the build differs from this design, and what was measured (2026-10-24)

- The compose test is `scripts/test_deploy.py` (stdlib unittest, run by `make unit`), not `backend/tests/integration/test_deploy.py`: the backend's ruff rules refuse the `docker` subprocess (`S603`, `S607`) and reading `os.environ` (`TID251`), and a suppression would raise the lock; `scripts/ruff.toml` already allows fixed-argument subprocesses.
- Images reference `tag@sha256:digest`, so Dependabot's `docker` and `docker-compose` ecosystems can update them.
- `net` is not removed by `nightly.sh` (as in the fallback's script): `web` and `api` join its namespace again on `up`.
- Measured locally with the compose project (dummy credentials, the warning fixtures in place of `/var/lib/tidehold-recorder`, no `ts`): migrate applied 0001–0018, ingest stored 260 warnings, `web` served 200; 21 workspace creations sent to `web` with `X-Forwarded-For: 203.0.113.50` gave 20 × 201 then 429, and another address 201, so Next and uvicorn carry the header as section 2 reads; the audit on an empty database logged 0 rows and exited 0; with a seeded pending open command it exited 1 with an `open` row, and the dump pipeline of `nightly.sh` wrote `money-<stamp>.sql` holding that command to the kept volume. Not measured locally: `nightly.sh` end to end (its `sed -i` is GNU sed, the VM's) and Tailscale Funnel's `X-Forwarded-For`; both are 10b.
