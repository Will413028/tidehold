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

log = logging.getLogger(__name__)

TICK_SECONDS = 5.0


def handlers(client: PayPalClient | None) -> dict[due.Kind, due.Handler]:
    async def reconcile_payment(conn: Conn, action: due.Action, _: datetime) -> None:
        if client is None or action.payment_id is None:
            raise due.RetryLater("PAYPAL_NOT_CONFIGURED")
        await service.reconcile_due(conn, client, action.payment_id)

    return {"request_balance": schedule.request_balance, "reconcile_payment": reconcile_payment}


async def tick(conn: Conn, run: Mapping[due.Kind, due.Handler], now: datetime) -> int:
    await schedule.ensure_schedules(conn)
    return await due.run_due(conn, run, now)


async def run_forever(database_url: str, run: Mapping[due.Kind, due.Handler]) -> None:
    while True:
        try:
            async with await connect(database_url) as conn:
                await tick(conn, run, datetime.now(UTC))
        except Exception:  # a failed tick is retried on the next one
            log.exception("due_actions tick failed")
        await asyncio.sleep(TICK_SECONDS)
