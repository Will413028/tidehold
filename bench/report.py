"""Turn `run.py` results into the tables of `model-benchmark.md` (plan step 2).

    python bench/report.py <results.json>

One row per model and split, averaged over its runs; latency percentiles over every compile
call of the model; 429s and 503s over all its runs.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import score


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def table(results: list[dict[str, Any]]) -> str:
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        runs[result["model"]].append(result)
    lines = [
        "| Model | Split | Structured | Spans located | Rule P / R | Ruling P / R |"
        " Event sets | p50 / p95 s | 429 / 503 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for model, model_runs in runs.items():
        latencies = [s for r in model_runs for f in r["fixtures"] for s in f["latencies_s"]]
        events = sum(r["summary"]["event_sets_exact"] for r in model_runs)
        event_total = sum(r["summary"]["event_sets"] for r in model_runs)
        # `run.py` records both counts cumulatively over a model's runs.
        limits = max(r["limits_hit"] for r in model_runs)
        unavailable = max(r.get("unavailable", 0) for r in model_runs)
        for split in ("dev", "held_out"):
            rows = [r["summary"]["splits"][split] for r in model_runs]
            ok = sum(row["structured_ok"] for row in rows)
            total = sum(row["compiles"] for row in rows)

            def mean(key: str, rows: list[dict[str, Any]] = rows) -> str:
                return _pct(_mean([row[key] for row in rows]))

            lines.append(
                f"| `{model}` | {split} | {ok}/{total} | {mean('span_located')} |"
                f" {mean('rule_precision')} / {mean('rule_recall')} |"
                f" {mean('ruling_precision')} / {mean('ruling_recall')} |"
                f" {events}/{event_total} |"
                f" {_seconds(score.percentile(latencies, 50))} /"
                f" {_seconds(score.percentile(latencies, 95))} | {limits} / {unavailable} |"
            )
    hashes = sorted({r["prompt_hash"] for r in results})
    started = min(r["started"] for r in results)
    return "\n".join(
        [f"Prompt hash: {', '.join(hashes)}; first run started {started}.", "", *lines]
    )


def main(argv: list[str]) -> int:
    print(table(json.loads(Path(argv[0]).read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
