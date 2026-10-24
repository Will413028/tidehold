"""Sandbox checks run by hand against PayPal's sandbox (plan step 4 acceptance).

    python -m tidehold.bootstrap.sandbox smoke
        Needs DATABASE_URL (a throwaway database) and PAYPAL_ENV=sandbox credentials. Runs the
        money flows through the money service: deposit capture then two partial refunds, and
        balance capture then a refund (D18). Then reads each order back and reports whether it
        lists every refund by tidehold's invoice id. Prints one JSON line.

Credentials come only from the environment (an --env-file); nothing here prints them.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from tidehold.identity.sessions import new_workspace
from tidehold.money import commands, service
from tidehold.money.paypal import TEST_CARD, PayPalClient, refunds_of
from tidehold.platform.db import Conn, connect
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import database_url_from_env, paypal_from_env

SMOKE_CAP = 10
AMOUNT = 3000  # USD 30.00

# name: (purpose, the refunds that follow the capture)
SMOKE_FLOWS: dict[str, tuple[commands.Purpose, list[int]]] = {
    "deposit_capture_then_two_refunds": ("deposit", [AMOUNT // 2, AMOUNT // 3]),
    "balance_capture_then_refund": ("balance", [AMOUNT // 3]),
}


async def _refunds_listed(
    client: PayPalClient, payment: Any, refunds: list[Any]
) -> list[dict[str, Any]]:
    """Read the payment's order back and find each refund by the invoice id tidehold sent: the
    read reconciliation relies on (D18 D4)."""
    if not refunds:
        return []
    listed = {r.invoice_id: r for r in refunds_of(await client.get_order(payment["order_id"]))}
    return [
        {
            "invoice_id": command["invoice_id"],
            "listed": command["invoice_id"] in listed,
            "status": getattr(listed.get(command["invoice_id"]), "status", None),
        }
        for command in refunds
    ]


async def smoke(conn: Conn, client: PayPalClient) -> dict[str, Any]:
    workspace, _ = await new_workspace(conn)
    report: dict[str, Any] = {}
    for name, (purpose, amounts) in SMOKE_FLOWS.items():
        payment_id = await commands.create_payment(
            conn, workspace_id=workspace, purpose=purpose, amount_minor=AMOUNT
        )
        await service.pay_with_test_card(conn, client, payment_id, TEST_CARD, daily_cap=SMOKE_CAP)
        outcomes, refunds = [], []
        for amount in amounts:
            async with conn.transaction():
                command = await commands.record_refund(
                    conn, payment_id, amount_minor=amount, now=datetime.now(UTC), proposal_id=None
                )
            outcomes.append(await service.execute(conn, client, command["id"]))
            refunds.append(command)
        payment = await commands.payment(conn, payment_id)
        report[name] = {
            "outcomes": outcomes,
            "state": payment["state"],
            "captured_minor": payment["captured_minor"],
            "refunded_minor": payment["refunded_minor"],
            "order_id": payment["order_id"],
            "capture_id": payment["capture_id"],
            "refunds_in_order": await _refunds_listed(client, payment, refunds),
        }
    return report


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _main(args: list[str]) -> dict[str, Any]:
    credentials = paypal_from_env()
    client = PayPalClient(credentials.client_id, credentials.client_secret)
    try:
        match args:
            case ["smoke"]:
                url = database_url_from_env()
                migrate(url)
                async with await connect(url) as conn:
                    return await smoke(conn, client)
            case _:
                raise SystemExit(__doc__)
    finally:
        await client.aclose()


if __name__ == "__main__":
    print(json.dumps({"ran_at": _now(), "report": asyncio.run(_main(sys.argv[1:]))}))
