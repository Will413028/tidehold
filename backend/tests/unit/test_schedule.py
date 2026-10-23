from datetime import UTC, date, datetime
from typing import Any

import pytest

from tidehold.holds.schedule import balance_due_at

INVITED = datetime(2026, 6, 1, tzinfo=UTC)


def _trip(**overrides: Any) -> dict[str, Any]:
    return {
        "starts_on": date(2026, 12, 5),
        "days": 3,
        "timezone": "Asia/Manila",
        "balance_due_days": 30,
        **overrides,
    }


@pytest.mark.parametrize(
    ("timezone", "starts_on", "due"),
    [
        # local midnight of 2026-11-05, 30 days before the first trip day
        ("Asia/Manila", date(2026, 12, 5), datetime(2026, 11, 4, 16, tzinfo=UTC)),
        ("America/Los_Angeles", date(2026, 12, 5), datetime(2026, 11, 5, 8, tzinfo=UTC)),
        # US daylight saving time ends on 2026-11-01, after that local midnight (UTC-7)
        ("America/Los_Angeles", date(2026, 12, 1), datetime(2026, 11, 1, 7, tzinfo=UTC)),
    ],
)
def test_the_balance_falls_due_its_days_before_the_first_local_trip_day(
    timezone: str, starts_on: date, due: datetime
) -> None:
    """A three-day trip, so counting from its last day would land two days later."""
    trip = _trip(timezone=timezone, starts_on=starts_on)

    assert balance_due_at(trip, INVITED) == due


def test_a_trip_booked_after_its_due_date_is_due_at_once() -> None:
    """Booked by a guest invited ten days before the trip, twenty days after its due date."""
    invited = datetime(2026, 11, 25, 9, 30, tzinfo=UTC)

    assert balance_due_at(_trip(), invited) == invited


def test_a_zero_day_balance_falls_due_when_the_first_local_trip_day_begins() -> None:
    due = balance_due_at(_trip(balance_due_days=0), INVITED)

    assert due == datetime(2026, 12, 4, 16, tzinfo=UTC)
