"""Evaluating a bound policy on a cancellation: code computes every amount, and anything the
policy does not decide, or anything past the trip's mandate, asks the operator (step 8a)."""

from typing import Any

import pytest

from tidehold.policy.schema import Rule
from tidehold.settlement.evaluate import (
    Cancelled,
    Evaluation,
    GuestMoney,
    Mandate,
    RuleIn,
    decide,
    evaluate,
    ruled_in_text,
)


def _rule(**overrides: Any) -> Rule:
    base: dict[str, Any] = {
        "product": None,
        "causes": ["weather"],
        "warning": None,
        "scope": "per_day",
        "applies_to": "both",
        "remedies": [
            {"kind": "refund", "percent": 100, "deductions": [], "credit_expiry_months": None}
        ],
        "chooser": None,
        "quote": "q",
    }
    return Rule.model_validate({**base, **overrides})


def _remedy(kind: str, percent: float, **overrides: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "percent": percent,
        "deductions": [],
        "credit_expiry_months": None,
        **overrides,
    }


ANA = GuestMoney("g-ana", deposit_base=5000, balance_base=30000)
DAY_2 = Cancelled(scope="day", cause="weather", first_day=2, covered_days=1, trip_days=3)
WHOLE = Cancelled(scope="rest", cause="weather", first_day=1, covered_days=3, trip_days=3)
ROOMY = Mandate(guest_minor=100_000, trip_minor=1_000_000)


def _lines(evaluation: Evaluation, purpose: str, option: int = 0) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = evaluation.parts[purpose][option]["lines"]
    return lines


def test_a_ruled_day_cancellation_inside_the_mandate_is_accepted_with_code_computed_amounts() -> (
    None
):
    evaluation = evaluate([RuleIn(_rule())], DAY_2, [ANA])

    assert evaluation.reasons == []
    assert _lines(evaluation, "deposit") == [
        {
            "guest_id": "g-ana",
            "kind": "refund",
            "amount_minor": 1666,
            "credit_expiry_months": None,
            "base_minor": 5000,
        }
    ]
    assert _lines(evaluation, "balance")[0]["amount_minor"] == 10000
    decision = decide(evaluation, ROOMY)
    assert decision.state == "accepted"
    assert decision.choice == {"deposit": 0, "balance": 0}


def test_a_cause_no_rule_covers_asks_with_standard_options() -> None:
    safety = Cancelled(scope="day", cause="safety", first_day=2, covered_days=1, trip_days=3)
    evaluation = evaluate([RuleIn(_rule())], safety, [ANA])

    assert "NO_RULE" in evaluation.reasons
    assert [o["label"] for o in evaluation.parts["deposit"]] == [
        "no remedy",
        "refund 100%",
        "credit 100%",
    ]
    assert decide(evaluation, ROOMY).state == "asked"


@pytest.mark.parametrize(
    ("reason", "rule"),
    [
        (
            "REMEDY_CHOICE",
            _rule(remedies=[_remedy("refund", 100), _remedy("credit", 100)], chooser="guest"),
        ),
        (
            "FIXED_DEDUCTION",
            _rule(
                remedies=[
                    _remedy(
                        "refund", 100, deductions=[{"kind": "fixed", "value": 50, "label": "fee"}]
                    )
                ]
            ),
        ),
    ],
)
def test_what_the_policy_leaves_open_always_asks(reason: str, rule: Rule) -> None:
    evaluation = evaluate([RuleIn(rule)], DAY_2, [ANA])

    assert reason in evaluation.reasons
    assert decide(evaluation, ROOMY).state == "asked"


def test_a_clause_ruled_in_free_text_always_asks() -> None:
    evaluation = evaluate([RuleIn(_rule(), ruled_in_text=True)], DAY_2, [ANA])

    assert evaluation.reasons == ["RULED_IN_TEXT"]
    assert decide(evaluation, ROOMY).state == "asked"


def test_a_warning_rule_without_a_matching_warning_asks_with_no_remedy_as_an_option() -> None:
    warning = {"named_as": "PAGASA", "source": "pagasa", "event_values": ["x"], "quote": "q"}
    evaluation = evaluate([RuleIn(_rule(warning=warning), warning_matched=False)], DAY_2, [ANA])

    assert evaluation.reasons == ["WARNING_NOT_MATCHED"]
    assert [o["label"] for o in evaluation.parts["balance"]] == ["refund 100%", "no remedy"]


def test_rules_that_disagree_ask_with_every_remedy_as_an_option() -> None:
    evaluation = evaluate(
        [RuleIn(_rule()), RuleIn(_rule(remedies=[_remedy("credit", 100)]))], DAY_2, [ANA]
    )

    assert evaluation.reasons == ["CONFLICTING_RULES"]
    assert len(evaluation.parts["deposit"]) == 2


def test_a_percent_deduction_is_exact() -> None:
    rule = _rule(
        scope="per_trip",
        remedies=[
            _remedy("refund", 100, deductions=[{"kind": "percent", "value": 3.5, "label": "fee"}])
        ],
    )
    evaluation = evaluate([RuleIn(rule)], WHOLE, [GuestMoney("g", 10000, 0)])

    assert _lines(evaluation, "deposit")[0]["amount_minor"] == 9650


def test_a_credit_carries_its_expiry() -> None:
    rule = _rule(remedies=[_remedy("credit", 100, credit_expiry_months=12)])
    line = _lines(evaluate([RuleIn(rule)], DAY_2, [ANA]), "deposit")[0]

    assert (line["kind"], line["credit_expiry_months"]) == ("credit", 12)


def test_a_per_trip_rule_settles_a_whole_trip_cancellation_only() -> None:
    rule = _rule(scope="per_trip")
    rest_from_day_2 = Cancelled(
        scope="rest", cause="weather", first_day=2, covered_days=2, trip_days=3
    )

    assert evaluate([RuleIn(rule)], WHOLE, [ANA]).reasons == []
    assert evaluate([RuleIn(rule)], rest_from_day_2, [ANA]).reasons == ["NO_RULE"]


def test_the_rest_of_a_trip_is_netted_against_what_earlier_proposals_returned() -> None:
    returned = GuestMoney("g-ana", 5000, 30000, returned={"deposit": 1666, "balance": 10000})
    rest = Cancelled(scope="rest", cause="weather", first_day=1, covered_days=3, trip_days=3)

    evaluation = evaluate([RuleIn(_rule())], rest, [returned])

    assert _lines(evaluation, "deposit")[0]["amount_minor"] == 5000 - 1666
    assert _lines(evaluation, "balance")[0]["amount_minor"] == 20000


def test_an_unplaced_payment_returns_nothing() -> None:
    evaluation = evaluate([RuleIn(_rule())], DAY_2, [GuestMoney("g", 5000, 0)])

    assert _lines(evaluation, "balance")[0]["amount_minor"] == 0


def test_returning_more_than_the_guest_cap_asks() -> None:
    evaluation = evaluate([RuleIn(_rule())], DAY_2, [ANA])

    decision = decide(evaluation, Mandate(guest_minor=11665, trip_minor=1_000_000))
    assert (decision.state, decision.reasons) == ("asked", ["MANDATE_GUEST_CAP"])


def test_the_trip_cap_counts_what_earlier_proposals_returned() -> None:
    evaluation = evaluate([RuleIn(_rule())], DAY_2, [ANA])

    inside = decide(evaluation, Mandate(guest_minor=100_000, trip_minor=20000))
    past = decide(evaluation, Mandate(guest_minor=100_000, trip_minor=20000, returned_trip=10000))
    assert inside.state == "accepted"
    assert (past.state, past.reasons) == ("asked", ["MANDATE_TRIP_CAP"])


@pytest.mark.parametrize(
    ("rule_span", "warning_span", "ruling_spans"),
    [((10, 20), None, [(15, 30)]), ((10, 20), (40, 50), [(45, 46)])],
)
def test_a_ruling_marks_the_rule_whose_clause_it_overlaps(
    rule_span: tuple[int, int],
    warning_span: tuple[int, int] | None,
    ruling_spans: list[tuple[int, int] | None],
) -> None:
    assert ruled_in_text(rule_span, warning_span, ruling_spans)


@pytest.mark.parametrize("ruling_spans", [[(20, 30)], [None]])
def test_a_ruling_that_quotes_another_clause_or_none_marks_nothing(
    ruling_spans: list[tuple[int, int] | None],
) -> None:
    assert not ruled_in_text((10, 20), None, ruling_spans)


def test_each_option_names_every_rule_it_came_from() -> None:
    """The guest page shows the clause behind an outcome (R12); a remedy two rules share is
    one option, and the standard options of an unruled cause come from no rule."""
    weather = _rule(quote="weather")
    shared = _rule(causes=["any"], quote="any cause")
    credit = _rule(remedies=[_remedy("credit", 100)], applies_to="deposit", quote="credit")

    evaluation = evaluate([RuleIn(weather), RuleIn(shared), RuleIn(credit)], DAY_2, [ANA])

    assert [(o["label"], o["rules"]) for o in evaluation.parts["deposit"]] == [
        ("refund 100%", [0, 1]),
        ("credit 100%", [2]),
    ]
    assert [o["rules"] for o in evaluation.parts["balance"]] == [[0, 1]]
    safety = Cancelled(scope="day", cause="safety", first_day=2, covered_days=1, trip_days=3)
    unruled = evaluate([RuleIn(weather)], safety, [ANA])
    assert all(o["rules"] == [] for o in unruled.parts["deposit"])
