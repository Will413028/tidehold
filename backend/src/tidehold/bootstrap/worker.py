"""The due_actions worker: every few seconds, derive schedules from trip state and run what
is due. One worker per host is enough (runtime ADR); the lease makes a second one safe."""

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from tidehold.holds import schedule
from tidehold.money import service
from tidehold.money.paypal import PayPalClient
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn, connect
from tidehold.settlement import drafts, execute, watch

log = logging.getLogger(__name__)

TICK_SECONDS = 5.0


NO_DRAFTS = drafts.Drafter(provider=None, daily_cap=0, draft_cap=0)


def handlers(
    client: PayPalClient | None, drafter: drafts.Drafter = NO_DRAFTS
) -> dict[due.Kind, due.Handler]:
    async def reconcile_payment(conn: Conn, action: due.Action, _: datetime) -> None:
        if client is None or action.payment_id is None:
            raise due.RetryLater("PAYPAL_NOT_CONFIGURED")
        await service.reconcile_due(conn, client, action.payment_id)

    async def settle_proposal(conn: Conn, action: due.Action, now: datetime) -> None:
        await execute.settle_due(conn, action, now, client)

    async def draft_ask(conn: Conn, action: due.Action, now: datetime) -> None:
        await drafts.draft_due(conn, action, now, drafter)

    return {
        "request_balance": schedule.request_balance,
        "reconcile_payment": reconcile_payment,
        "check_trip": watch.check_trip,
        "settle_proposal": settle_proposal,
        "draft_ask": draft_ask,
    }


async def tick(conn: Conn, run: Mapping[due.Kind, due.Handler], now: datetime) -> int:
    await schedule.ensure_schedules(conn)
    await watch.ensure_schedules(conn, now)
    await execute.ensure_schedules(conn, now)
    return await due.run_due(conn, run, now)


async def run_forever(database_url: str, run: Mapping[due.Kind, due.Handler]) -> None:
    while True:
        try:
            async with await connect(database_url) as conn:
                await tick(conn, run, datetime.now(UTC))
        except Exception:  # a failed tick is retried on the next one
            log.exception("due_actions tick failed")
        await asyncio.sleep(TICK_SECONDS)
