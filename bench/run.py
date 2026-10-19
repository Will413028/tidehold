"""Run the compile benchmark (plan step 2) against live providers.

    cd backend && uv run --frozen python ../bench/run.py [--runs 2] [--models groq:<id>,...]
        [--split dev|held_out|all] [--out ../bench/results/<file>.json]

Keys come from GROQ_API_KEY and GEMINI_API_KEY (`tidehold.platform.settings`; on the VM via
`--env-file`, D13). Calls are sequential and spaced per provider so the fallback demo sharing
the keys keeps its quota; a 429 is counted, waited out once, then recorded as a failure. The
compile path is the product's own (`tidehold.policy`), so the prompt hash recorded here is the
one step 5 ships.
"""

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx2 as httpx

import score
from tidehold.platform.settings import ModelKeys, model_keys_from_env
from tidehold.policy.compile import CompileFailed, compile_policy, propose_event_set
from tidehold.policy.prompt import PROMPT_HASH
from tidehold.policy.providers import Gemini, Groq, Provider, ProviderError
from tidehold.warnings.catalogue import GROUPS

HERE = Path(__file__).parent
CANDIDATES = (
    "groq:openai/gpt-oss-120b",
    "groq:openai/gpt-oss-20b",
    "groq:qwen/qwen3.8-27b",
    "gemini:gemini-3.8-flash",
    "gemini:gemini-3.5-flash",
    "gemini:gemini-3.1-pro-preview",
    "gemini:gemma-4-31b-it",
)
# Seconds between calls per provider: under the free tiers' per-minute limits with room left
# for the fallback demo (Groq 30 requests/min, Gemini flash 10/min, read 2026-10-19).
SPACING = {"groq": 4.0, "gemini": 10.0}
RATE_LIMIT_WAIT_S = 65.0
# One call and, after a 429 or a 503, one more once the minute has passed.
TRIES = 2
SOURCES = ["pagasa", "nws", "meteoalarm"]

T = TypeVar("T")


@dataclass
class Outcome:
    fixture: str
    split: str
    ok: bool
    error: str | None = None
    attempts: int = 0
    latencies_s: list[float] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    kept: int = 0
    lost: int = 0


@dataclass
class EventOutcome:
    case: str
    ok: bool
    exact: bool
    values: list[str]
    refused: list[str]
    error: str | None = None
    latency_s: float | None = None


@dataclass
class Pacer:
    spacing: float
    last: float = 0.0
    limits_hit: int = 0
    unavailable: int = 0

    async def call(self, make: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(1, TRIES + 1):
            wait = self.last + self.spacing - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()
            try:
                return await make()
            except ProviderError as error:
                if not error.transient or attempt == TRIES:
                    raise
                if error.rate_limited:
                    self.limits_hit += 1
                else:
                    self.unavailable += 1
                await asyncio.sleep(RATE_LIMIT_WAIT_S)
        raise AssertionError("unreachable")


def _provider(spec: str, client: httpx.AsyncClient, keys: ModelKeys) -> Provider:
    vendor, model = spec.split(":", 1)
    if vendor == "groq" and keys.groq:
        return Groq(client, keys.groq, model)
    if vendor == "gemini" and keys.gemini:
        return Gemini(client, keys.gemini, model)
    raise SystemExit(f"no key or unknown vendor for {spec}")


async def _fixture(provider: Provider, pacer: Pacer, name: str, split: str) -> Outcome:
    wording = json.loads((HERE / "fixtures" / f"{name}.json").read_text())["wording"]
    outcome = Outcome(name, split, ok=False)
    try:
        compiled = await pacer.call(lambda: compile_policy(provider, wording, SOURCES))
    except (CompileFailed, ProviderError) as error:
        outcome.error = f"{type(error).__name__}: {error}"[:300]
        return outcome
    outcome.ok = True
    outcome.attempts = compiled.attempts
    outcome.latencies_s = compiled.latencies_s
    outcome.rules = [located.rule.model_dump() for located in compiled.rules]
    outcome.topics = [r.ruling.topic for r in compiled.needs_ruling]
    outcome.kept = len(compiled.rules)
    outcome.lost = outcome.topics.count("span_not_found")
    return outcome


async def _event_set(provider: Provider, pacer: Pacer, case: dict[str, Any]) -> EventOutcome:
    source = case["source"]
    try:
        proposal = await pacer.call(
            lambda: propose_event_set(provider, case["wording"], source, GROUPS[source])
        )
    except (CompileFailed, ProviderError) as error:
        return EventOutcome(
            case["id"],
            ok=False,
            exact=False,
            values=[],
            refused=[],
            error=f"{type(error).__name__}: {error}",
        )
    exact = set(proposal.values) == set(case["expected"]) and not proposal.refused
    return EventOutcome(
        case["id"],
        ok=True,
        exact=exact,
        values=proposal.values,
        refused=proposal.refused,
        latency_s=proposal.latency_s,
    )


def summarise(
    labels: dict[str, Any], outcomes: list[Outcome], events: list[EventOutcome]
) -> dict[str, Any]:
    by_split: dict[str, Any] = {}
    for split in ("dev", "held_out"):
        rows = [o for o in outcomes if o.split == split]
        rule_counts, topic_counts = score.NONE, score.NONE
        for o in rows:
            label = labels["fixtures"][o.fixture]
            if o.ok:
                rule_counts += score.rules(o.rules, label["rules"])
                topic_counts += score.topics(o.topics, label["needs_ruling"])
            else:  # a failed compile finds nothing it should have found
                rule_counts += score.Counts(0, 0, len(label["rules"]))
                topic_counts += score.Counts(0, 0, len(set(label["needs_ruling"])))
        kept = sum(o.kept for o in rows)
        lost = sum(o.lost for o in rows)
        by_split[split] = {
            "compiles": len(rows),
            "structured_ok": sum(o.ok for o in rows),
            "span_located": kept / (kept + lost) if kept + lost else None,
            "rule_precision": rule_counts.precision,
            "rule_recall": rule_counts.recall,
            "ruling_precision": topic_counts.precision,
            "ruling_recall": topic_counts.recall,
        }
    latencies = [s for o in outcomes for s in o.latencies_s]
    return {
        "splits": by_split,
        "event_sets_exact": sum(e.exact for e in events),
        "event_sets": len(events),
        "latency_p50_s": score.percentile(latencies, 50),
        "latency_p95_s": score.percentile(latencies, 95),
    }


async def bench(spec: str, runs: int, split: str, keys: ModelKeys) -> list[dict[str, Any]]:
    labels = json.loads((HERE / "labels.json").read_text())
    fixtures = [
        (name, label["split"])
        for name, label in labels["fixtures"].items()
        if split == "all" or label["split"] == split
    ]
    results = []
    async with httpx.AsyncClient() as client:
        provider = _provider(spec, client, keys)
        pacer = Pacer(SPACING[spec.split(":", 1)[0]])
        for run in range(1, runs + 1):
            started = datetime.now(UTC)
            outcomes = [await _fixture(provider, pacer, n, s) for n, s in fixtures]
            events = [await _event_set(provider, pacer, c) for c in labels["event_sets"]]
            results.append(
                {
                    "model": spec,
                    "run": run,
                    "prompt_hash": PROMPT_HASH,
                    "labels_version": labels["version"],
                    "started": started.isoformat(),
                    "limits_hit": pacer.limits_hit,
                    "unavailable": pacer.unavailable,
                    "summary": summarise(labels, outcomes, events),
                    "fixtures": [asdict(o) for o in outcomes],
                    "event_sets": [asdict(e) for e in events],
                }
            )
            print(spec, run, json.dumps(results[-1]["summary"]), flush=True)
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(CANDIDATES))
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--split", choices=("dev", "held_out", "all"), default="all")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    keys = model_keys_from_env()
    results: list[dict[str, Any]] = []
    for spec in args.models.split(","):
        results += asyncio.run(bench(spec, args.runs, args.split, keys))
        args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
