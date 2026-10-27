# tidehold demo deploy

Plan step 10 (design: `docs/plans/2026-10-19-tidehold-hackathon/step10a-design.md`). A compose project of its own (`-p tidehold`) on the VM that also runs the dive-trip-agent fallback demo; nothing here touches that stack, its timer, volumes or secrets. PayPal sandbox and synthetic data only.

## Services

| Service | What | Reachable from |
|---|---|---|
| `db` | PostgreSQL 18, user `tidehold` | the project network |
| `migrate` | one-off migration | nothing |
| `ingest` | one-off: the recorder's files (`/var/lib/tidehold-recorder`, read-only) into the evidence store | nothing |
| `net` | holds the network namespace `api` and `web` share | nothing |
| `api` | FastAPI and the `due_actions` worker, on `127.0.0.1:8000` inside `net` | `web` only |
| `web` | Next production server on `:3000`, proxies `/api` to the API | `ts` only |
| `ts` | Tailscale node `tidehold` (`tag:tidehold-demo`), Funnel to `web` | the public URL |
| `audit` | maintenance profile: the nightly read-only reconciliation | nothing |

No host port is published. Containers are read-only (except `db` and `ts`), drop all capabilities, run with `no-new-privileges`, memory and CPU limits, and rotated logs.

## Host files (never committed)

`/home/ubuntu/tidehold/` holds this directory (`deploy/`) and `tidehold.env`, no sources:

```sh
TIDEHOLD_REVISION=<git short SHA>
TIDEHOLD_ORIGIN=https://tidehold.<tailnet>.ts.net
TIDEHOLD_SECRETS_DIR=/home/ubuntu/.secrets/tidehold
SHARED_SECRETS_DIR=/home/ubuntu/.secrets/dive-deposits-demo
```

`SHARED_SECRETS_DIR` is the fallback's directory (D13): `paypal.env` is used by path, unchanged. `TIDEHOLD_SECRETS_DIR` (`chmod 700`) holds `db_password` (random; mode 644 so the non-root containers can read it, the directory keeps other host users out) and, optionally, `anthropic.env` (`ANTHROPIC_API_KEY`; without it the operator writes rules by hand). Nobody prints or reads these files; they are created by the developer.

## Build and load

The Mac and the VM are both ARM64: build at the exact SHA locally and load the images on the VM.

```sh
REV=$(git rev-parse --short HEAD)
docker build -f deploy/backend.Dockerfile -t tidehold-backend:$REV .
docker build -f deploy/web.Dockerfile -t tidehold-web:$REV .
docker save tidehold-backend:$REV tidehold-web:$REV | gzip | ssh oci-a1 'gunzip | docker load'
```

## First start (step 10b)

The tailnet policy needs `tag:tidehold-demo` in `tagOwners` and in the Funnel `nodeAttrs`. Then:

```sh
cd /home/ubuntu/tidehold/deploy
set -a; . ../tidehold.env; set +a
docker compose -p tidehold -f compose.yaml up -d
docker compose -p tidehold -f compose.yaml logs ts   # the login URL, approved once
```

The image's containerboot gives `tailscale up` 60 seconds and then restarts the container with a new login URL, so a URL from `logs ts` is valid for about a minute. To get one that stays valid, log the node in with `tailscaled` alone on the same state volume, then start `ts` normally (done this way on 2026-10-25):

```sh
docker compose -p tidehold -f compose.yaml stop ts
docker compose -p tidehold -f compose.yaml run -d --no-deps --name tidehold-ts-login \
  --entrypoint tailscaled ts --tun=userspace-networking --statedir=/var/lib/tailscale \
  --socket=/tmp/ts-login.sock
docker exec tidehold-ts-login tailscale --socket=/tmp/ts-login.sock up \
  --hostname=tidehold --advertise-tags=tag:tidehold-demo   # prints the URL; approve it
docker rm -f tidehold-ts-login
docker compose -p tidehold -f compose.yaml up -d ts
```

## Every night, and new revisions

`tidehold-nightly.timer` (11:00 UTC) runs `nightly.sh`: stop `web` and `api`, run `audit`, remove the app and the database (`tidehold_db-data`), `up -d`. A failed audit (a mismatch, a command still open, a failed read) also writes a dump of the money tables next to its log, and the script exits non-zero after the rebuild. The log is `reconcile-<date>.jsonl` on the `tidehold_reconcile-log` volume:

```sh
docker run --rm -v tidehold_reconcile-log:/log:ro --entrypoint tail \
  tidehold-backend:$TIDEHOLD_REVISION -n 1 /log/reconcile-$(date -u +%F).jsonl
```

A new revision goes through the same script: `TIDEHOLD_NEXT_REVISION=<sha> ./nightly.sh`.

```sh
sudo cp tidehold-nightly.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tidehold-nightly.timer
```

## Rollback and removal

```sh
docker compose -p tidehold -f compose.yaml down          # keeps volumes
sudo systemctl disable --now tidehold-nightly.timer
```

`down` acts on the `tidehold` project only; never pass `--volumes` with another project's name. Removing the `tidehold` device in the Tailscale admin retires the hostname.
