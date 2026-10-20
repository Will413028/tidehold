"""The periodic `check_trip` due action: ask the operator about what may need a cancellation
(plan step 8a, D16). A warning never moves money; it becomes an ask, once per trip day and
event value. From the first trip day on, a guest with no authorized balance is asked about.

Schedules are derived from trip state on every worker tick (runtime ADR): a trip in its
window gets a `check_trip` when none is open, due 10 minutes after the last one finished.
The handler re-reads the trip, so a cancellation since the action was scheduled only changes
what it finds (D17).
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn
from tidehold.policy import versions
from tidehold.settlement import matching, proposals
from tidehold.trips import store as trips
from tidehold.warnings import catalogue

EVERY = timedelta(minutes=10)
BEFORE_START = timedelta(days=2)


def window(trip: DictRow) -> tuple[datetime, datetime]:
    """From two days before the first local trip day to the end of the last one."""
    start, _ = trips.day_window(trip["starts_on"], 1, trip["timezone"])
    _, end = trips.day_window(trip["starts_on"], trip["days"], trip["timezone"])
    return start - BEFORE_START, end


async def ensure_schedules(conn: Conn, now: datetime) -> int:
    """Give every trip inside its window, not cancelled as a whole, one open `check_trip`."""
    rows = await (
        await conn.execute(
            "select t.* from trips t"
            " where t.cancelled_at is null"
            " and not exists (select 1 from due_actions d where d.trip_id = t.id"
            "   and d.kind = 'check_trip' and d.state in ('pending', 'running'))"
            " and t.starts_on <= %s::date + 3 and t.starts_on + t.days >= %s::date - 1",
            (now, now),
        )
    ).fetchall()
    scheduled = 0
    for trip in rows:
        start, end = window(trip)
        if not start <= now < end:
            continue
        last = await (
            await conn.execute(
                "select max(finished_at) as at from due_actions"
                " where trip_id = %s and kind = 'check_trip'",
                (trip["id"],),
            )
        ).fetchone()
        due_at = max(start, last["at"] + EVERY) if last and last["at"] else start
        await due.schedule(conn, "check_trip", trip["id"], due_at=due_at)
        scheduled += 1
    return scheduled


def _event_sets(version: DictRow, product: str | None) -> set[str]:
    return {
        value
        for rule in versions.rules_of(version)
        if rule.warning is not None and (product is None or rule.product in (None, product))
        for value in rule.warning.event_values
    }


def _warning_question(row: DictRow, *, known: bool) -> str:
    when = f"from {row['onset'].isoformat()} to {row['expires'].isoformat()}"
    head = (
        f"{row['event']} ({row['source'].upper()}) is in force for {row['area_desc']}"
        f" on trip day {row['trip_day']}, {when}."
    )
    if not known:
        head += (
            f" Its event is not in tidehold's {row['source']} catalogue, so no rule can match it."
        )
    return f"{head} Cancel that day or the rest of the trip, or dismiss this ask."


async def _warning_asks(conn: Conn, trip: DictRow, now: datetime) -> None:
    rows = await conn.execute(
        "select trip_day, scope from cancellations where trip_id = %s and withdrawn_at is null",
        (trip["id"],),
    )
    cancelled = await rows.fetchall()
    rest_from = min((c["trip_day"] for c in cancelled if c["scope"] == "rest"), default=None)
    open_days = [
        d
        for d in range(1, trip["days"] + 1)
        if d not in {c["trip_day"] for c in cancelled} and (rest_from is None or d < rest_from)
    ]
    if not open_days:
        return
    version = await versions.version(conn, trip["workspace_id"], trip["policy_version_id"])
    bound = _event_sets(version, trip["policy_product"])
    known = (
        catalogue.values(trip["warning_source"])
        if trip["warning_source"] in catalogue.GROUPS
        else frozenset()
    )
    for row in await matching.in_force(conn, trip, open_days, now):
        value = matching.value(row)
        if value in known and value not in bound:
            continue
        facts: dict[str, Any] = {"warning": matching.summary(row), "event_set": sorted(bound)}
        if value not in known:
            facts["reason"] = "UNKNOWN_EVENT"
        await proposals.ask(
            conn,
            trip,
            proposals.NewAsk(
                "warning",
                f"day:{row['trip_day']}:{value}",
                _warning_question(row, known=value in known),
                facts,
            ),
        )


async def _guest_asks(conn: Conn, trip: DictRow, now: datetime) -> None:
    first_day, _ = trips.day_window(trip["starts_on"], 1, trip["timezone"])
    if now < first_day:
        return
    for guest in await trips.guests(conn, trip["id"]):
        if guest["balance_state"] in ("authorized", "captured"):
            continue
        await proposals.ask(
            conn,
            trip,
            proposals.NewAsk(
                "unauthorized_guest",
                f"guest:{guest['id']}",
                f"{guest['name']} has not authorized the trip balance, and the trip started"
                f" on {trip['starts_on'].isoformat()}. Settle it with the guest, then dismiss"
                " this ask.",
                {"guest": guest["name"], "balance_state": guest["balance_state"]},
            ),
        )


async def check_trip(conn: Conn, action: due.Action, now: datetime) -> None:
    if action.trip_id is None:
        raise ValueError("check_trip needs a trip")
    await check(conn, action.trip_id, now)


async def check(conn: Conn, trip_id: UUID, now: datetime) -> None:
    """Open the asks the trip's state calls for now; the periodic action and a replay (step 9)
    both run it."""
    async with conn.transaction():
        # locked like a cancellation, so an ask is never opened on a day cancelled meanwhile
        trip = await (
            await conn.execute("select * from trips where id = %s for update", (trip_id,))
        ).fetchone()
        if trip is None or trip["cancelled_at"] is not None:
            return
        await _warning_asks(conn, trip, now)
        await _guest_asks(conn, trip, now)
