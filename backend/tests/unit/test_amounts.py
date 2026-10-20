from tidehold.money.amounts import usd
from tidehold.settlement.guest import Outcome, _explanation


def test_amounts_are_written_as_the_web_writes_them() -> None:
    # the format of formatUsd in web/src/lib/money.ts
    assert [usd(m) for m in (0, 5, 1666, 10000, -250)] == [
        "USD 0.00",
        "USD 0.05",
        "USD 16.66",
        "USD 100.00",
        "-USD 2.50",
    ]


def test_a_guest_explanation_names_the_days_the_warnings_and_each_outcome() -> None:
    facts = {"cancellation": {"whole_trip": False, "cause": "weather"}}
    warnings = [
        {"event": "Gale Warning", "source": "nws"},
        {"event": "Storm Warning", "source": "nws"},
    ]
    outcomes = [
        Outcome("deposit", "credit", 2500, None, []),
        Outcome("balance", "none", 0, None, []),
    ]

    assert _explanation([2, 3], facts, warnings, outcomes) == (
        "The operator cancelled days 2 to 3 for weather."
        " Gale Warning (NWS), Storm Warning (NWS) were in force."
        " Your deposit: USD 25.00 as credit. Your balance: no change."
    )
