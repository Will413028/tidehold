"""Fail CI when a compile benchmark run falls below the floors (`.github/workflows/eval.yml`).

    python bench/gate.py <results.json>

Prints the `report.py` table, then each failure; exits 1 on any. Only the dev split is gated:
the prompt is tuned on dev, and reading held-out results on every change would tune on them
too (GUIDELINE "Splits").

Two kinds of check. Contract checks hold in every run: every compile returns the schema, and
every kept rule quotes the operator's wording (AGENTS.md "Product contract"). Quality floors
apply to the mean over runs, because one run of five fixtures varies a lot at temperature 0
(model-benchmark.md, step 5 "Reading"). The floors sit about 25 points under the Sonnet 5.5
dev row at prompt 00bdb5c3b65fa352 (rules 73% / 80%, rulings 86% / 100%, event sets 8/8):
they catch a prompt or model change that breaks the compile, not a one-fixture difference.
"""

import json
import sys
from pathlib import Path
from typing import Any

import report

FLOORS = {
    "rule_precision": 0.5,
    "rule_recall": 0.5,
    "ruling_precision": 0.6,
    "ruling_recall": 0.7,
}
EVENT_SETS_FLOOR = 0.75
NAMES = {
    "rule_precision": "rule precision",
    "rule_recall": "rule recall",
    "ruling_precision": "ruling precision",
    "ruling_recall": "ruling recall",
}


def _contract(rows: list[dict[str, Any]]) -> list[str]:
    found = []
    ok = sum(row["structured_ok"] for row in rows)
    total = sum(row["compiles"] for row in rows)
    if ok < total:
        found.append(f"dev structured output {ok}/{total}, every compile must return the schema")
    located = [row["span_located"] for row in rows if row["span_located"] is not None]
    if located and min(located) < 1:
        found.append(
            f"dev spans located {min(located):.0%} in a run, every kept rule must quote the wording"
        )
    return found


def _quality(rows: list[dict[str, Any]]) -> list[str]:
    found = []
    for key, floor in FLOORS.items():
        # A failed compile scores None for precision; it is already a contract failure.
        values = [row[key] for row in rows if row[key] is not None]
        mean = sum(values) / len(values) if values else 0.0
        # Rounded so that a mean landing on the floor through float error is not a failure.
        if round(mean, 6) < floor:
            found.append(f"dev {NAMES[key]} {mean:.0%} below the floor {floor:.0%}")
    return found


def failures(results: list[dict[str, Any]]) -> list[str]:
    if not results:
        return ["no benchmark runs in the results"]
    rows = [r["summary"]["splits"]["dev"] for r in results]
    found = _contract(rows) + _quality(rows)
    exact = sum(r["summary"]["event_sets_exact"] for r in results)
    total = sum(r["summary"]["event_sets"] for r in results)
    if total and exact / total < EVENT_SETS_FLOOR:
        found.append(f"event sets exact {exact}/{total} below the floor {EVENT_SETS_FLOOR:.0%}")
    return found


def main(argv: list[str]) -> int:
    results = json.loads(Path(argv[0]).read_text())
    if results:
        print(report.table(results))
    found = failures(results)
    print()
    print("\n".join(f"- FAIL: {f}" for f in found) if found else "All floors met.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
