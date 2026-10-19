"""When a trip's balance hold opens: T-(29 - N) for an N-day trip (deposit/balance ADR, D11).

A hold placed then is at most 28 days old on the trip's last day, inside PayPal's 29-day
authorization validity. Schedules are derived from trip state on every worker tick, so a
missed or lost action is recreated (runtime ADR: actions are rebuilt from trip state).
"""

from datetime import UTC, date, datetime, time, timedelta

from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn
from tidehold.trips import store

AUTHORIZATION_VALID_DAYS = 29


def balance_opens_at(starts_on: date, days: int) -> datetime:
    """Midnight UTC of T-(29 - N). A trip booked later than that opens at once."""
    opens_on = starts_on - timedelta(days=AUTHORIZATION_VALID_DAYS - days)
    return datetime.combine(opens_on, time(), UTC)


async def ensure_schedules(conn: Conn) -> int:
    """Give every trip whose balance is not open yet its one request_balance action."""
    rows = await (
        await conn.execute(
            "select t.id, t.starts_on, t.days from trips t"
            " where t.balance_requested_at is null and not exists ("
            "   select 1 from due_actions d where d.trip_id = t.id and d.kind = 'request_balance')"
        )
    ).fetchall()
    for row in rows:
        await due.schedule(
            conn,
            "request_balance",
            trip_id=row["id"],
            due_at=balance_opens_at(row["starts_on"], row["days"]),
        )
    return len(rows)


async def request_balance(conn: Conn, action: due.Action, now: datetime) -> None:
    """The request_balance due action: open the hold to the trip's guests."""
    if action.trip_id is None:
        raise ValueError("request_balance needs a trip")
    await store.open_balances(conn, action.trip_id, now)
