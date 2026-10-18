# recorder

Records official warning feeds so the demo can replay real warnings with their original fetch times (plan step 1, D12). Standard library only, no app dependencies.

- `record.py`: one run fetches every source's index feed, then the linked warning documents not recorded yet (at most 60 per source per run). Every response, non-200 and network errors included, becomes one gzipped JSON file `<out>/<source>/<YYYY-MM-DD>/<fetched_at>-<index|document>-<url hash>.json.gz` with `source`, `url`, `fetched_at` (UTC), `http_status` (null on a network error, with `error`), `content_type` and the raw `body` (UTF-8, or base64 with `body_encoding`). `<out>/<source>/seen.txt` lists documents already recorded with 200.
- `check.py DIR`: every file has the metadata fields; every source has at least 4 consecutive index fetches no more than 45 minutes apart.
- Sources and what they carry: `docs/plans/2026-10-19-tidehold-hackathon/feeds-matrix.md`.

## Run

```sh
python3 -m unittest -v test_recorder          # from this directory
python3 -I record.py --out /tmp/recordings     # one live run
python3 -I check.py /tmp/recordings --min-consecutive 1
```

## On the VM

`deploy/install.sh [host]` (default `oci-a1`) installs `record.py` and `check.py` to `/opt/tidehold-recorder`, creates the `tidehold-rec` system user, installs `tidehold-recorder.service` and `.timer` (every 30 minutes) and runs once. Recordings go to `/var/lib/tidehold-recorder`, outside any demo database.

```sh
ssh oci-a1 'python3 -I /opt/tidehold-recorder/check.py /var/lib/tidehold-recorder'
ssh oci-a1 'journalctl -u tidehold-recorder.service -n 20 -o cat'
```

Remove: `sudo systemctl disable --now tidehold-recorder.timer`, then delete the two units in `/etc/systemd/system/` and `/opt/tidehold-recorder`. Keep `/var/lib/tidehold-recorder` until step 12 is done; it is the replay source.
