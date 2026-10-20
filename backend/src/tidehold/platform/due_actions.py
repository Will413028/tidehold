"""Timed work on a PostgreSQL table, claimed under a lease (D1, runtime ADR).

A worker claims one due action (`FOR UPDATE SKIP LOCKED`), commits, and only then does the
work, so a slow PayPal or model call holds no lock. A worker that stalls past its lease
loses the action to the next claimer; its late completion is ignored because completing
names the attempt it claimed. Handlers must therefore be idempotent, which money commands
already are (stored before sent, one live command per step).

Time is passed in (`now`), so tests drive the clock. One exception: `schedule` without a
`due_at` reads the database clock, for work that is due "soon" from a caller that has no
clock (an unknown PayPal outcome inside `money.service.execute`).
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from tidehold.platform.db import Conn

Kind = Literal["request_balance", "reconcile_payment", "check_trip", "settle_proposal", "draft_ask"]

log = logging.getLogger(__name__)

# A handler must finish inside its kind's lease, or another worker takes the action over.
# Kinds that call PayPal make calls of at most 20 s each; `settle_proposal` makes up to two per
# guest (12 guests a trip), and `draft_ask` calls a model whose timeout is 90 s
# (`policy.providers.TIMEOUT_S`), so their leases are longer.
LEASES: dict[Kind, timedelta] = {
    "request_balance": timedelta(seconds=60),
    "reconcile_payment": timedelta(seconds=60),
    "check_trip": timedelta(seconds=60),
    "settle_proposal": timedelta(seconds=600),
    "draft_ask": timedelta(seconds=120),
}
LEASE = max(LEASES.values())
MAX_ATTEMPTS = 8
# What each kind acts on; the subject goes in that column (`due_actions_one_subject`).
SUBJECT: dict[Kind, Literal["trip", "payment", "proposal"]] = {
    "request_balance": "trip",
    "reconcile_payment": "payment",
    "check_trip": "trip",
    "settle_proposal": "proposal",
    "draft_ask": "proposal",
}


@dataclass(frozen=True)
class Action:
    id: int
    kind: Kind
    trip_id: UUID | None
    payment_id: UUID | None
    proposal_id: UUID | None
    due_at: datetime
    attempts: int


type Handler = Callable[[Conn, Action, datetime], Awaitable[None]]


class RetryLater(Exception):
    """The handler did what it could; run the action again after a backoff."""


def backoff(attempts: int) -> timedelta:
    """30 s, 1 min, 2 min, ... capped at 30 min."""
    return min(timedelta(seconds=30 * 2 ** (attempts - 1)), timedelta(minutes=30))


async def schedule(conn: Conn, kind: Kind, subject: UUID, *, due_at: datetime | None) -> None:
    """Ensure one open action of this kind for the subject (a trip, a payment or a proposal,
    per `SUBJECT`); an open one is left as it is. Without `due_at`, the action is due a minute
    from the database's clock."""
    columns = {"trip": None, "payment": None, "proposal": None, SUBJECT[kind]: subject}
    await conn.execute(
        "insert into due_actions (kind, trip_id, payment_id, proposal_id, due_at)"
        " values (%s, %s, %s, %s, coalesce(%s, now() + interval '1 minute'))"
        " on conflict (kind, coalesce(trip_id, payment_id, proposal_id))"
        " where state in ('pending', 'running') do nothing",
        (kind, columns["trip"], columns["payment"], columns["proposal"], due_at),
    )


async def claim(
    conn: Conn, now: datetime, leases: Mapping[Kind, timedelta] = LEASES
) -> Action | None:
    """Claim the oldest due action, or one whose worker's lease expired, under its kind's lease."""
    seconds = {kind: int(lease.total_seconds()) for kind, lease in leases.items()}
    row = await (
        await conn.execute(
            "update due_actions set state = 'running',"
            " lease_until = %(now)s + make_interval(secs => (%(leases)s::jsonb ->> kind)::int),"
            " attempts = attempts + 1"
            " where id = (select id from due_actions"
            "   where (state = 'pending' and due_at <= %(now)s)"
            "      or (state = 'running' and lease_until <= %(now)s)"
            "   order by due_at, id for update skip locked limit 1)"
            " returning id, kind, trip_id, payment_id, proposal_id, due_at, attempts",
            {"now": now, "leases": Jsonb(seconds)},
        )
    ).fetchone()
    return Action(**row) if row else None


async def complete(conn: Conn, action: Action, now: datetime) -> bool:
    """False when the lease was lost: another worker owns the action now."""
    row = await (
        await conn.execute(
            "update due_actions set state = 'done', lease_until = null, finished_at = %s"
            " where id = %s and state = 'running' and attempts = %s returning id",
            (now, action.id, action.attempts),
        )
    ).fetchone()
    return row is not None


async def retry(conn: Conn, action: Action, now: datetime, error: str) -> bool:
    """Back off, or give up after MAX_ATTEMPTS; a failed action stays for the operator console
    (plan step 9) and is not recreated by schedules derived from trip state."""
    failed = action.attempts >= MAX_ATTEMPTS
    row = await (
        await conn.execute(
            "update due_actions set state = %s, lease_until = null, due_at = %s,"
            " finished_at = %s, last_error = %s"
            " where id = %s and state = 'running' and attempts = %s returning id",
            (
                "failed" if failed else "pending",
                now + backoff(action.attempts),
                now if failed else None,
                error[:500],
                action.id,
                action.attempts,
            ),
        )
    ).fetchone()
    return row is not None


async def run_due(
    conn: Conn, handlers: Mapping[Kind, Handler], now: datetime, *, limit: int = 20
) -> int:
    """Run up to `limit` due actions; returns how many were claimed."""
    ran = 0
    while ran < limit and (action := await claim(conn, now)) is not None:
        ran += 1
        try:
            await handlers[action.kind](conn, action, now)
        except RetryLater as signal:
            await retry(conn, action, now, str(signal) or "RETRY_LATER")
            continue
        except Exception as error:  # an unexpected failure must not stop the other actions
            log.exception("due action %s (%s) failed", action.id, action.kind)
            await retry(conn, action, now, type(error).__name__)
            continue
        await complete(conn, action, now)
    return ran
