"""The periodic `check_trip` due action: ask the operator about what may need a cancellation
(plan step 8a, D16), and about balances still unpaid when they fall due (step 8b, D21). A
warning never moves money; it becomes an ask, once per trip day and event value.

Schedules are derived from trip state on every worker tick (runtime ADR): a trip inside its
warning window (two days before the trip to its end) gets a `check_trip` when none is open,
due 10 minutes after the last one finished; before that window, a check runs only once an
unpaid balance has become the operator's question (`holds.schedule.unpaid_ask_at`), a known
instant, so a trip due 120 days ahead does not poll for 120 days. The handler re-reads the
trip, so a cancellation since the action was scheduled only changes what it finds (D17).

This module also opens the `command_open` asks, from a sweep: `money` cannot import
`settlement`, so a command no read of PayPal could decide is found here.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.rows import DictRow

from tidehold.holds.schedule import balance_due_at, unpaid_ask_at
from tidehold.money.amounts import usd
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn
from tidehold.policy import versions
from tidehold.settlement import matching, proposals
from tidehold.trips import store as trips
from tidehold.warnings import catalogue

EVERY = timedelta(minutes=10)
BEFORE_START = timedelta(days=2)


def window(trip: DictRow) -> tuple[datetime, datetime]:
    """The warning window: from two days before the first local trip day to the end of the
    last one."""
    start, _ = trips.day_window(trip["starts_on"], 1, trip["timezone"])
    _, end = trips.day_window(trip["starts_on"], trip["days"], trip["timezone"])
    return start - BEFORE_START, end


def _unpaid(guest: DictRow) -> bool:
    """A guest still owing its balance: not released, not captured, something left due."""
    return (
        guest["released_at"] is None
        and guest["balance_state"] != "captured"
        and guest["balance_minor"] > guest["balance_waived_minor"]
    )


async def _next_unpaid_ask(conn: Conn, trip: DictRow) -> datetime | None:
    """When the next unpaid balance not yet asked about becomes the operator's question."""
    asked = {
        row["subject"]
        for row in await (
            await conn.execute(
                "select subject from proposals where trip_id = %s and kind = 'unpaid_balance'",
                (trip["id"],),
            )
        ).fetchall()
    }
    return min(
        (
            unpaid_ask_at(trip, g["created_at"])
            for g in await trips.guests(conn, trip["id"])
            if _unpaid(g) and f"guest:{g['id']}" not in asked
        ),
        default=None,
    )


async def _due_at(conn: Conn, trip: DictRow, now: datetime) -> datetime | None:
    start, end = window(trip)
    if now >= end:
        return None
    if now >= start:
        last = await (
            await conn.execute(
                "select max(finished_at) as at from due_actions"
                " where trip_id = %s and kind = 'check_trip'",
                (trip["id"],),
            )
        ).fetchone()
        return max(start, last["at"] + EVERY) if last and last["at"] else start
    # before the warning window only an unpaid balance can ask; the schedule is derived again
    # on every tick, so a check is scheduled once one is due rather than ahead of time (a guest
    # invited later may be due sooner)
    unpaid = await _next_unpaid_ask(conn, trip)
    return now if unpaid is not None and unpaid <= now else None


async def ensure_schedules(conn: Conn, now: datetime) -> int:
    """Give every trip not over, not cancelled as a whole, one open `check_trip`."""
    rows = await (
        await conn.execute(
            "select t.* from trips t"
            " where t.cancelled_at is null"
            " and not exists (select 1 from due_actions d where d.trip_id = t.id"
            "   and d.kind = 'check_trip' and d.state in ('pending', 'running'))"
            " and t.starts_on + t.days >= %s::date - 1",
            (now,),
        )
    ).fetchall()
    scheduled = 0
    for trip in rows:
        due_at = await _due_at(conn, trip, now)
        if due_at is None:
            continue
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


async def _unpaid_asks(conn: Conn, trip: DictRow, now: datetime) -> None:
    """From its ask time on (`unpaid_ask_at`), a balance still unpaid asks the operator to
    wait or release the guest (D21); the ask stays open until answered."""
    zone = ZoneInfo(trip["timezone"])
    for guest in await trips.guests(conn, trip["id"]):
        if not _unpaid(guest) or now < unpaid_ask_at(trip, guest["created_at"]):
            continue
        due_minor = guest["balance_minor"] - guest["balance_waived_minor"]
        due_on = balance_due_at(trip, guest["created_at"]).astimezone(zone).date()
        await proposals.ask(
            conn,
            trip,
            proposals.NewAsk(
                "unpaid_balance",
                f"guest:{guest['id']}",
                f"{guest['name']} has not paid the trip balance ({usd(due_minor)}), due on"
                f" {due_on.isoformat()}. Wait (the guest's link stays open), or release the"
                " guest: nothing more is charged and no money moves.",
                {"guest": guest["name"], "due_minor": due_minor, "due_on": due_on.isoformat()},
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
        await _unpaid_asks(conn, trip, now)


# Open commands a read of PayPal could not decide: PayPal refused the resend, or the payment's
# last `reconcile_payment` failed after reading this command (the same test `execute._being_read`
# makes for a settlement's refund).
UNDECIDED = (
    "select c.id, c.kind, c.amount_minor, c.error_code, c.result, c.resend_refused_at,"
    " p.purpose, g.name, t.id as trip_id, t.workspace_id"
    " from money_commands c join payments p on p.id = c.payment_id"
    " join guests g on p.id in (g.deposit_payment_id, g.balance_payment_id)"
    " join trips t on t.id = g.trip_id"
    " where c.status in ('pending', 'unknown') and c.sent_at is not null"
    " and (c.resend_refused_at is not null or coalesce(("
    "   select d.state = 'failed' and c.read_at >= d.created_at from due_actions d"
    "   where d.kind = 'reconcile_payment' and d.payment_id = c.payment_id"
    "   order by d.id desc limit 1), false))"
    " and not exists (select 1 from proposals q where q.trip_id = t.id"
    "   and q.kind = 'command_open' and q.subject = 'command:' || c.id)"
)


def _command_question(row: DictRow) -> str:
    what = "refund" if row["kind"] == "refund" else "payment"
    why = (
        "PayPal refused to take it again"
        if row["resend_refused_at"] is not None
        else "reading PayPal kept failing"
    )
    head = (
        f"tidehold cannot tell whether the {what} of {row['name']}'s {row['purpose']}"
        f" ({usd(row['amount_minor'])}) went through: {why}."
    )
    if row["kind"] == "refund":
        return f"{head} Check PayPal, then answer found or not found."
    return f"{head} Check PayPal: answer not found if it shows no capture."


async def open_command_asks(conn: Conn) -> int:
    """Ask the operator about every open command no read could decide, once each (plan step
    8b): the answer closes the command from what PayPal shows, so it does not stay open."""
    opened = 0
    for row in await (await conn.execute(UNDECIDED)).fetchall():
        paypal = {k: v for k, v in (row["result"] or {}).items() if k != "resolvedBy"}
        asked = await proposals.ask(
            conn,
            {"workspace_id": row["workspace_id"], "id": row["trip_id"]},
            proposals.NewAsk(
                "command_open",
                f"command:{row['id']}",
                _command_question(row),
                {
                    "guest": row["name"],
                    "purpose": row["purpose"],
                    "kind": row["kind"],
                    "amount_minor": row["amount_minor"],
                    "code": row["error_code"],
                    "paypal": paypal,
                },
            ),
        )
        opened += asked is not None
    return opened
