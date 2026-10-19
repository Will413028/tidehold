"""Request bodies shared by the HTTP tests."""

from datetime import date
from typing import Any

# A trip far enough out that its balance hold is not open yet (T-(29 - N) is in the future).
TRIP: dict[str, Any] = {
    "name": "Anilao weekend",
    "starts_on": "2026-12-05",
    "days": 3,
    "site_lat": 13.7565,
    "site_lon": 120.9137,
    "warning_source": "pagasa",
    "warning_areas": ["041000000"],
    "deposit_minor": 5000,
    "balance_minor": 30000,
}


def trip_starting(on: date, days: int = 3) -> dict[str, Any]:
    return {**TRIP, "starts_on": on.isoformat(), "days": days}
