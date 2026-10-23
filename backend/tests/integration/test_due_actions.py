"""The due_actions lease on a real database (plan step 6 acceptance, D15)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tidehold.identity.sessions import new_workspace
from tidehold.money import commands
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn, connect

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


async def _payment(conn: Conn) -> UUID:
    workspace, _ = await new_workspace(conn)
    return await commands.create_payment(
        conn, workspace_id=workspace, purpose="balance", intent="authorize", amount_minor=1000
    )


async def _state(conn: Conn) -> list[tuple[str, int]]:
    rows = await (await conn.execute("select state, attempts from due_actions")).fetchall()
    return [(r["state"], r["attempts"]) for r in rows]


async def test_two_concurrent_claimers_run_an_action_once(conn: Conn, database_url: str) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)

    async with await connect(database_url) as a, await connect(database_url) as b:
        async with a.transaction():
            first = await due.claim(a, T0)
            # The row is locked by a's open transaction: b skips it instead of waiting.
            blocked = await due.claim(b, T0)
        raced = await asyncio.gather(due.claim(a, T0), due.claim(b, T0))

    assert first is not None
    assert blocked is None
    assert all(r is None for r in raced)  # the lease is still valid after a commits
    assert await _state(conn) == [("running", 1)]


async def test_an_expired_lease_is_reclaimed_exactly_once(conn: Conn, database_url: str) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    stalled = await due.claim(conn, T0)
    later = T0 + due.LEASE + timedelta(seconds=1)

    async with await connect(database_url) as b, await connect(database_url) as c:
        taken = await asyncio.gather(due.claim(b, later), due.claim(c, later))

    winners = [t for t in taken if t is not None]
    assert stalled is not None
    assert len(winners) == 1
    assert await due.complete(conn, stalled, later) is False  # the stalled worker lost it
    assert await due.complete(conn, winners[0], later) is True
    assert await _state(conn) == [("done", 2)]


async def test_an_action_still_inside_its_lease_is_not_reclaimed(conn: Conn) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    await due.claim(conn, T0)

    assert (
        await due.claim(conn, T0 + due.LEASES["reconcile_payment"] - timedelta(seconds=1)) is None
    )


async def test_a_failing_action_backs_off_then_gives_up(conn: Conn) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    runs: list[datetime] = []

    async def flaky(_: Conn, __: due.Action, now: datetime) -> None:
        runs.append(now)
        raise due.RetryLater("PAYPAL_STILL_UNKNOWN")

    handlers: dict[due.Kind, due.Handler] = {"reconcile_payment": flaky}
    now = T0
    for _ in range(due.MAX_ATTEMPTS):
        assert await due.run_due(conn, handlers, now) == 1
        assert await due.run_due(conn, handlers, now + timedelta(seconds=1)) == 0
        now += timedelta(hours=1)

    assert len(runs) == due.MAX_ATTEMPTS
    assert await _state(conn) == [("failed", due.MAX_ATTEMPTS)]


async def test_scheduling_the_same_work_twice_keeps_one_action(conn: Conn) -> None:
    payment_id = await _payment(conn)

    await due.schedule(conn, "reconcile_payment", payment_id, due_at=T0)
    await due.schedule(conn, "reconcile_payment", payment_id, due_at=T0 + timedelta(1))

    assert await _state(conn) == [("pending", 0)]


async def test_each_kind_is_claimed_under_its_own_lease(conn: Conn) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    leases: dict[due.Kind, timedelta] = {
        "request_balance": timedelta(minutes=5),
        "reconcile_payment": timedelta(seconds=7),
    }

    await due.claim(conn, T0, leases)
    row = await (await conn.execute("select lease_until from due_actions")).fetchone()

    assert row is not None
    assert row["lease_until"] == T0 + timedelta(seconds=7)


async def _failures(conn: Conn) -> list[tuple[str, int]]:
    rows = await (await conn.execute("select state, failures from due_actions")).fetchall()
    return [(r["state"], r["failures"]) for r in rows]


async def test_waiting_is_not_a_failure_and_starts_the_failures_in_a_row_over(conn: Conn) -> None:
    """MAX_ATTEMPTS - 1 failures, a wait, then one more failure: still pending, because the
    wait reset the count; and the claims (`attempts`) went past MAX_ATTEMPTS without failing."""
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    signals: list[Exception] = [
        *(due.RetryLater("STILL_UNKNOWN") for _ in range(due.MAX_ATTEMPTS - 1)),
        due.Waiting(timedelta(hours=1), "PENDING"),
        due.RetryLater("STILL_UNKNOWN"),
    ]

    async def handler(_: Conn, __: due.Action, ___: datetime) -> None:
        raise signals.pop(0)

    now = T0
    while signals:
        assert await due.run_due(conn, {"reconcile_payment": handler}, now) == 1
        now += timedelta(hours=2)

    assert await _failures(conn) == [("pending", 1)]
    assert await _state(conn) == [("pending", due.MAX_ATTEMPTS + 1)]


async def test_a_lease_taken_over_counts_as_a_failure(conn: Conn) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    await due.claim(conn, T0)

    taken = await due.claim(conn, T0 + due.LEASE + timedelta(seconds=1))

    assert taken is not None
    assert taken.failures == 1


async def test_a_worker_that_lost_its_lease_cannot_make_the_action_wait(conn: Conn) -> None:
    await due.schedule(conn, "reconcile_payment", await _payment(conn), due_at=T0)
    stalled = await due.claim(conn, T0)
    assert stalled is not None
    later = T0 + due.LEASE + timedelta(seconds=1)
    assert await due.claim(conn, later) is not None

    waited = await due.wait(conn, stalled, later, due.Waiting(timedelta(hours=1), "PENDING"))

    assert not waited
    assert await _failures(conn) == [("running", 1)]
