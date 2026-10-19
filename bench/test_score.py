import hashlib
import json
from pathlib import Path
from typing import Any

import score

HERE = Path(__file__).parent
LABELS = json.loads((HERE / "labels.json").read_text())


def _rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "product": None,
        "causes": ["weather", "safety"],
        "warning": None,
        "scope": "per_day",
        "applies_to": "both",
        "remedies": [
            {"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": None}
        ],
        "chooser": None,
        "quote": "x",
    }
    return rule | overrides


def test_rules_match_regardless_of_cause_order_product_and_quote() -> None:
    expected = [_rule()]
    predicted = [_rule(causes=["safety", "weather", "weather"], product="Fun dives", quote="y")]

    counts = score.rules(predicted, expected)

    assert (counts.hits, counts.precision, counts.recall) == (1, 1.0, 1.0)


def test_a_different_remedy_percent_or_chooser_is_not_a_match() -> None:
    expected = [_rule()]
    refund = [{"kind": "refund", "percent": 100, "deductions": [], "credit_expiry_months": None}]

    assert score.rules([_rule(remedies=refund)], expected).hits == 0
    assert score.rules([_rule(chooser="guest")], expected).hits == 0
    assert score.rules([_rule(), _rule()], expected) == score.Counts(1, 2, 1)


def test_flagging_every_topic_costs_precision() -> None:
    everything = [
        "warning_source_unnamed",
        "remedy_chooser_unclear",
        "outcome_unspecified",
        "operator_discretion",
        "no_weather_clause",
        "span_not_found",
    ]

    counts = score.topics(everything, ["no_weather_clause"])

    assert counts.recall == 1.0
    assert counts.precision == 0.2


def test_percentile_is_nearest_rank() -> None:
    assert score.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 50) == 3.0
    assert score.percentile([5.0, 1.0, 3.0, 2.0, 4.0], 95) == 5.0
    assert score.percentile([], 50) is None


def test_every_labelled_quote_is_in_its_fixture_and_splits_are_fixed() -> None:
    splits = {}
    for name, label in LABELS["fixtures"].items():
        fixture = json.loads((HERE / "fixtures" / f"{name}.json").read_text())
        wording = fixture["wording"]
        assert hashlib.sha256(wording.encode()).hexdigest() == fixture["sha256"]
        assert all(rule["quote"] in wording for rule in label["rules"])
        splits[name] = label["split"]

    assert sorted(n for n, s in splits.items() if s == "held_out") == [
        "aquatic-apes",
        "dive-komodo",
        "dragon-dive",
        "kaia",
    ]
