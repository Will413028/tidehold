#!/usr/bin/env bash
# Install or update the recorder on HOST (default oci-a1) and run it once.
# Touches only /opt/tidehold-recorder, /var/lib/tidehold-recorder and the tidehold-recorder units.
set -euo pipefail
host="${1:-oci-a1}"
here="$(cd "$(dirname "$0")/.." && pwd)"
stage="/tmp/tidehold-recorder-install"

ssh "$host" "rm -rf $stage && mkdir -p $stage"
scp -q "$here/record.py" "$here/check.py" "$here/deploy/tidehold-recorder.service" "$here/deploy/tidehold-recorder.timer" "$host:$stage/"
ssh "$host" "set -euo pipefail
  id tidehold-rec >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin tidehold-rec
  sudo install -d -m 0755 /opt/tidehold-recorder
  sudo install -m 0644 $stage/record.py $stage/check.py /opt/tidehold-recorder/
  sudo install -m 0644 $stage/tidehold-recorder.service $stage/tidehold-recorder.timer /etc/systemd/system/
  rm -rf $stage
  sudo systemctl daemon-reload
  sudo systemctl enable --now tidehold-recorder.timer
  sudo systemctl start tidehold-recorder.service
  systemctl --no-pager --lines=0 status tidehold-recorder.service || true
  systemctl list-timers --no-pager tidehold-recorder.timer"
