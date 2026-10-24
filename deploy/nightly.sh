#!/usr/bin/env bash
# Nightly reset of the tidehold demo (plan step 10a): reconcile the day's money commands with
# PayPal (reads only), then rebuild the database from scratch. ts and its state stay up, so the
# public hostname does not change; the reconciliation log volume is kept.
#
# Deploying a new revision is the same rebuild: set TIDEHOLD_NEXT_REVISION, and the running
# revision (whose code matches the current schema) audits before the next one migrates an
# empty database. Run from this directory with the TIDEHOLD_* and SHARED_SECRETS_DIR variables
# of README.md.
set -euo pipefail
: "${TIDEHOLD_REVISION:?}" "${TIDEHOLD_ORIGIN:?}" "${TIDEHOLD_SECRETS_DIR:?}" "${SHARED_SECRETS_DIR:?}"
compose=(docker compose -p tidehold -f compose.yaml)

# Stop the app first so no command is recorded or settled while the audit reads.
"${compose[@]}" stop web api
status=0
"${compose[@]}" --profile maintenance run --rm audit || status=$?
if [ "$status" -ne 0 ]; then
  # The rebuild deletes what would explain the failure: keep the money tables beside the log.
  stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
  "${compose[@]}" exec -T db pg_dump -U tidehold -d tidehold --data-only \
    -t payments -t money_commands -t payment_events \
    | "${compose[@]}" --profile maintenance run --rm -T --entrypoint sh audit \
      -c "cat > /var/log/tidehold/money-${stamp}.sql" || status=2
fi
"${compose[@]}" --profile maintenance rm --stop --force web api ingest migrate audit db
docker volume rm tidehold_db-data
export TIDEHOLD_REVISION="${TIDEHOLD_NEXT_REVISION:-$TIDEHOLD_REVISION}"
"${compose[@]}" up -d
# Later nightly runs read tidehold.env; keep it on the revision now serving.
sed -i "s/^TIDEHOLD_REVISION=.*/TIDEHOLD_REVISION=${TIDEHOLD_REVISION}/" ../tidehold.env
# The demo must come back each morning, so the rebuild runs after a failed audit; the non-zero
# exit makes the timer report it.
exit "$status"
