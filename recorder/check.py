"""Check a recorder directory: every file has the metadata fields, every source has a run of consecutive index fetches.

Usage: python3 -I check.py DIR [--min-consecutive 4] [--max-gap-minutes 45] [--expect NAME ...]
Exit 0 when every file is valid and every source (each subdirectory, plus --expect) has
at least --min-consecutive index fetches with no gap longer than --max-gap-minutes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

REQUIRED = ("source", "url", "fetched_at", "http_status")


def file_problem(path: Path, record: dict) -> str | None:  # noqa: PLR0911
    missing = [key for key in REQUIRED if key not in record]
    if missing:
        return f"{path}: missing {', '.join(missing)}"
    if not isinstance(record["http_status"], (int, type(None))):
        return f"{path}: http_status is not an integer"
    if record["http_status"] is None and not record.get("error"):
        return f"{path}: no http_status and no error"
    if not record["url"] or not record["source"]:
        return f"{path}: empty source or url"
    try:
        dt.datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return f"{path}: fetched_at is not ISO 8601"
    if record["source"] != path.parent.parent.name:
        return f"{path}: source {record['source']!r} does not match its directory"
    return None


def longest_run(times: list[dt.datetime], max_gap: dt.timedelta) -> int:
    best = run = 0
    previous = None
    for t in sorted(times):
        run = run + 1 if previous is not None and t - previous <= max_gap else 1
        best = max(best, run)
        previous = t
    return best


def check(root: Path, min_consecutive: int, max_gap: dt.timedelta, expect: list[str]) -> tuple[list[str], list[dict]]:
    problems: list[str] = []
    rows: list[dict] = []
    sources = sorted({p.name for p in root.iterdir() if p.is_dir()} | set(expect))
    for name in sources:
        index_times: list[dt.datetime] = []
        statuses: dict[str, int] = {}
        files = sorted((root / name).glob("*/*.json.gz")) if (root / name).is_dir() else []
        for path in files:
            try:
                record = json.loads(gzip.decompress(path.read_bytes()))
            except (OSError, ValueError) as exc:
                problems.append(f"{path}: unreadable ({exc})")
                continue
            problem = file_problem(path, record)
            if problem:
                problems.append(problem)
                continue
            if record.get("kind") == "index":
                index_times.append(dt.datetime.fromisoformat(record["fetched_at"].replace("Z", "+00:00")))
                key = str(record["http_status"])
                statuses[key] = statuses.get(key, 0) + 1
        run = longest_run(index_times, max_gap)
        rows.append({"source": name, "files": len(files), "index_fetches": len(index_times), "longest_run": run,
                     "index_statuses": statuses, "last_fetch": max(index_times).isoformat() if index_times else None})
        if run < min_consecutive:
            problems.append(f"{name}: longest run of consecutive index fetches is {run}, need {min_consecutive}")
    return problems, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--min-consecutive", type=int, default=4)
    parser.add_argument("--max-gap-minutes", type=float, default=45)
    parser.add_argument("--expect", nargs="*", default=[])
    args = parser.parse_args(argv)
    problems, rows = check(args.root, args.min_consecutive, dt.timedelta(minutes=args.max_gap_minutes), args.expect)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
