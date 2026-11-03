from typing import Any

import gate


def _run(**dev: Any) -> dict[str, Any]:
    split = {
        "compiles": 5,
        "structured_ok": 5,
        "span_located": 1.0,
        "rule_precision": 0.75,
        "rule_recall": 0.8,
        "ruling_precision": 0.85,
        "ruling_recall": 1.0,
    } | dev
    return {
        "model": "anthropic:claude-sonnet-5-5",
        "prompt_hash": "00bdb5c3b65fa352",
        "started": "2026-11-03T00:00:00+00:00",
        "limits_hit": 0,
        "unavailable": 0,
        "summary": {"splits": {"dev": split}, "event_sets_exact": 8, "event_sets": 8},
        "fixtures": [],
    }


def test_runs_at_the_benchmarked_level_pass() -> None:
    assert gate.failures([_run(), _run(rule_recall=0.6)]) == []


def test_a_compile_without_structured_output_fails_the_gate() -> None:
    assert gate.failures([_run(), _run(structured_ok=4)]) == [
        "dev structured output 9/10, every compile must return the schema"
    ]


def test_a_rule_whose_quote_is_not_in_the_wording_fails_the_gate() -> None:
    assert gate.failures([_run(span_located=0.9), _run()]) == [
        "dev spans located 90% in a run, every kept rule must quote the wording"
    ]


def test_quality_is_judged_on_the_mean_over_runs_not_one_noisy_run() -> None:
    # One run at 20% and one at 80% average 50%, the floor: noise in a single run of five
    # fixtures does not fail the gate, a drop in both runs does.
    assert gate.failures([_run(rule_recall=0.2), _run(rule_recall=0.8)]) == []
    assert gate.failures([_run(rule_recall=0.2), _run(rule_recall=0.6)]) == [
        "dev rule recall 40% below the floor 50%"
    ]


def test_missed_event_sets_fail_the_gate() -> None:
    runs = [_run(), _run()]
    runs[1]["summary"]["event_sets_exact"] = 3
    assert gate.failures(runs) == ["event sets exact 11/16 below the floor 75%"]


def test_no_results_fail_the_gate() -> None:
    assert gate.failures([]) == ["no benchmark runs in the results"]
