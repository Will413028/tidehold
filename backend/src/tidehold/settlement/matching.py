"""Does an official warning satisfy a rule's warning condition (D16 ADR, matching)?

A warning matches when it comes from the trip's source for one of the trip's areas, carries an
event value in the rule's confirmed set, is `Actual` and not replaced by a visible Update or
Cancel, was visible at the decision time, and was in force during a covered local trip day.
The evidence store applies the visibility and lifecycle conditions; this module adds the day
windows and the event set. Membership only: no text is read.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, cast

from psycopg.rows import DictRow

from tidehold.platform.db import Conn
from tidehold.trips import store as trips
from tidehold.warnings import catalogue, store
from tidehold.warnings.cap import Source


def value(row: DictRow) -> str:
    return catalogue.value_of(row["source"], row["event"], row["parameters"])


async def in_force(conn: Conn, trip: DictRow, days: Sequence[int], at: datetime) -> list[DictRow]:
    """Warnings from the trip's source for its areas, visible at `at`, in force on any of
    `days`; each row carries the trip day it was found on (`trip_day`)."""
    found: list[DictRow] = []
    for day in days:
        query = store.EvidenceQuery(
            workspace_id=trip["workspace_id"],
            source=cast("Source", trip["warning_source"]),
            areas=list(trip["warning_areas"]),
            decided_at=at,
            window=trips.day_window(trip["starts_on"], day, trip["timezone"]),
        )
        found.extend({**row, "trip_day": day} for row in await store.evidence(conn, query))
    return found


def matching(rows: Sequence[DictRow], event_values: Collection[str]) -> list[DictRow]:
    return [row for row in rows if value(row) in event_values]


def summary(row: DictRow) -> dict[str, Any]:
    """What an ask or a settlement shows of a warning, as visible at its decision time."""
    return {
        "source": row["source"],
        "identifier": row["identifier"],
        "event": row["event"],
        "value": value(row),
        "area_desc": row["area_desc"],
        "onset": row["onset"].isoformat(),
        "expires": row["expires"].isoformat(),
        "recorded_at": row["recorded_at"].isoformat(),
        "visible_at": row["visible_at"].isoformat(),
        "replayed": row["replayed"],
        "url": row["url"],
        "trip_day": row["trip_day"],
    }
