from datetime import date, timedelta

import pytest

from tidehold.holds.schedule import AUTHORIZATION_VALID_DAYS, balance_opens_at
from tidehold.trips.store import MAX_TRIP_DAYS, ends_on


@pytest.mark.parametrize("days", range(1, MAX_TRIP_DAYS + 1))
def test_the_last_trip_day_falls_on_day_28_of_the_hold(days: int) -> None:
    starts_on = date(2026, 12, 5)

    opened = balance_opens_at(starts_on, days)
    last_day = ends_on(starts_on, days)

    assert last_day - opened.date() == timedelta(days=AUTHORIZATION_VALID_DAYS - 1)


def test_a_sixty_day_out_trip_is_not_held_at_booking() -> None:
    booked = date(2026, 10, 6)
    starts_on = booked + timedelta(days=60)

    assert balance_opens_at(starts_on, 3).date() == date(2026, 11, 9)
