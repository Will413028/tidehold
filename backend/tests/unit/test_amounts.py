from tidehold.money.amounts import usd
from tidehold.settlement.guest import _explanation


def test_amounts_are_written_as_the_web_writes_them() -> None:
    # the format of formatUsd in web/src/lib/money.ts
    assert [usd(m) for m in (0, 5, 1666, 10000, -250)] == [
        "USD 0.00",
        "USD 0.05",
        "USD 16.66",
        "USD 100.00",
        "-USD 2.50",
    ]


def test_a_guest_explanation_names_the_days_and_the_warnings_but_no_amount() -> None:
    facts = {"cancellation": {"whole_trip": False, "cause": "weather"}}
    warnings = [
        {"event": "Gale Warning", "source": "nws"},
        {"event": "Storm Warning", "source": "nws"},
    ]

    assert _explanation([2, 3], facts, warnings, decided=True) == (
        "The operator cancelled days 2 to 3 for weather."
        " Gale Warning (NWS), Storm Warning (NWS) were in force."
    )


def test_a_warning_seen_live_and_replayed_is_named_once() -> None:
    facts = {"cancellation": {"whole_trip": False, "cause": "weather"}}
    gale = {"event": "Gale Warning", "source": "nws"}

    sentence = _explanation([1], facts, [gale, gale], decided=False)

    assert sentence.startswith(
        "The operator cancelled day 1 for weather. Gale Warning (NWS) was in force."
    )
