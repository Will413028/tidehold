"""Sandbox checks run by hand against PayPal's sandbox (plan step 4 acceptance).

    python -m tidehold.bootstrap.sandbox smoke
        Needs DATABASE_URL (a throwaway database) and PAYPAL_ENV=sandbox credentials. Runs the
        money flows through the money service: deposit capture then two partial refunds;
        balance capture then a refund (D18); balance hold then partial final capture; balance
        hold then void (the hold flows until plan step 8c). Then reads each order back and
        reports whether it lists every refund by tidehold's invoice id. Prints one JSON line.

    python -m tidehold.bootstrap.sandbox reauth create|authorize|day4 STATE_FILE
        The reauthorization check of the deposit/balance ADR. `create` (day 0) holds two
        card authorizations and creates one PayPal-account order whose approval link a
        person opens with a sandbox buyer; `authorize` authorizes that order once approved;
        `day4` (day 4 or later) reauthorizes the first card and the PayPal-account hold and
        captures each new authorization, and captures the second card's original
        authorization without reauthorizing. STATE_FILE keeps the IDs and request
        ids between days, outside any rebuilt database; request ids are written before each
        call, so a rerun after a crash repeats it instead of moving money twice.

Credentials come only from the environment (an --env-file); nothing here prints them.
"""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tidehold.identity.sessions import new_workspace
from tidehold.money import commands, service
from tidehold.money.paypal import (
    TEST_CARD,
    Authorization,
    PayPalClient,
    PayPalError,
    PayPalRedirect,
    PayPalUnknownOutcome,
    authorization_of,
    order_id_of,
    refunds_of,
)
from tidehold.platform.db import Conn, connect
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import database_url_from_env, paypal_from_env

SMOKE_CAP = 10
# Where PayPal sends the sandbox buyer after approving; nothing needs to answer there.
RETURN_URL = "https://example.com/tidehold-sandbox-approved"
AMOUNT = 3000  # USD 30.00


# name: (purpose, intent, the follow-up commands and their amounts)
SMOKE_FLOWS: dict[
    str, tuple[commands.Purpose, commands.Intent, list[tuple[commands.Kind, int]]]
] = {
    "deposit_capture_then_two_refunds": (
        "deposit",
        "capture",
        [("refund", AMOUNT // 2), ("refund", AMOUNT // 3)],
    ),
    "balance_capture_then_refund": ("balance", "capture", [("refund", AMOUNT // 3)]),
    # the hold flows stay until plan step 8c (D18)
    "balance_hold_then_partial_final_capture": (
        "balance",
        "authorize",
        [("capture", AMOUNT * 2 // 3)],
    ),
    "balance_hold_then_void": ("balance", "authorize", [("void", 0)]),
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
    for name, (purpose, intent, steps) in SMOKE_FLOWS.items():
        payment_id = await commands.create_payment(
            conn, workspace_id=workspace, purpose=purpose, intent=intent, amount_minor=AMOUNT
        )
        await service.pay_with_test_card(conn, client, payment_id, TEST_CARD, daily_cap=SMOKE_CAP)
        outcomes, refunds = [], []
        for kind, amount in steps:
            async with conn.transaction():
                command = await (
                    commands.record_refund(
                        conn,
                        payment_id,
                        amount_minor=amount,
                        now=datetime.now(UTC),
                        proposal_id=None,
                    )
                    if kind == "refund"
                    else commands.record_command(conn, payment_id, kind, amount_minor=amount)
                )
            outcomes.append(await service.execute(conn, client, command["id"]))
            if kind == "refund":
                refunds.append(command)
        payment = await commands.payment(conn, payment_id)
        report[name] = {
            "outcomes": outcomes,
            "state": payment["state"],
            "captured_minor": payment["captured_minor"],
            "refunded_minor": payment["refunded_minor"],
            "order_id": payment["order_id"],
            "authorization_id": payment["authorization_id"],
            "capture_id": payment["capture_id"],
            "refunds_in_order": await _refunds_listed(client, payment, refunds),
        }
    return report


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load(path: Path) -> dict[str, Any]:
    # A one-shot command: blocking file access is fine here.
    state: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def _key(state: dict[str, Any], path: Path, name: str) -> str:
    """The request id for one step, stored before the call that uses it."""
    keys = state.setdefault("request_ids", {})
    if name not in keys:
        keys[name] = f"tidehold-reauth-{name}-{uuid4()}"
        _save(path, state)
    key: str = keys[name]
    return key


async def reauth_create(client: PayPalClient, path: Path) -> dict[str, Any]:
    state = _load(path)
    for card in ("card_reauthorize", "card_capture_only"):
        if card not in state:
            order = await client.create_order(
                intent="AUTHORIZE",
                invoice_id=_key(state, path, f"{card}-invoice"),
                amount_minor=AMOUNT,
                request_id=_key(state, path, card),
                source=TEST_CARD,
            )
            auth = authorization_of(order)
            state[card] = {
                "authorization_id": auth.authorization_id,
                "status": auth.status,
                "created_at": _now(),
                "expires_at": auth.expires_at,
            }
            _save(path, state)
    if "paypal_account" not in state:
        order = await client.create_order(
            intent="AUTHORIZE",
            invoice_id=_key(state, path, "paypal_account-invoice"),
            amount_minor=AMOUNT,
            request_id=_key(state, path, "paypal_account"),
            source=PayPalRedirect(RETURN_URL),
        )
        links = {link.get("rel"): link.get("href") for link in order.get("links", [])}
        state["paypal_account"] = {
            "order_id": order_id_of(order),
            "approve_url": links.get("payer-action") or links.get("approve"),
            "created_at": _now(),
        }
        _save(path, state)
    return state


async def reauth_authorize(client: PayPalClient, path: Path) -> dict[str, Any]:
    state = _load(path)
    account = state["paypal_account"]
    if "authorization_id" not in account:
        order = await client.authorize_order(
            account["order_id"], request_id=_key(state, path, "paypal_account-authorize")
        )
        auth = authorization_of(order)
        account.update(
            authorization_id=auth.authorization_id,
            status=auth.status,
            authorized_at=_now(),
            expires_at=auth.expires_at,
        )
        _save(path, state)
    return state


async def _step(
    state: dict[str, Any], path: Path, step: str, call: Callable[[str], Awaitable[Any]]
) -> Any:
    """Run one day-4 step until PayPal answers, keyed by name; record every attempt.

    An `unknown` answer is resent under the same request id on the next run, so a
    timeout neither repeats the move nor ends the check without a result."""
    results: list[dict[str, Any]] = state.setdefault("day4", [])
    if any(r["step"] == step and r["result"] != "unknown" for r in results):
        return None
    record: dict[str, Any] = {"step": step, "at": _now()}
    result = None
    try:
        result = await call(_key(state, path, step))
    except PayPalError as error:
        record.update(result="refused", code=error.code)
    except PayPalUnknownOutcome as error:
        record.update(result="unknown", code=error.code)
    else:
        record.update(result="accepted", detail=repr(result))
    results.append(record)
    _save(path, state)
    return result


async def reauth_day4(client: PayPalClient, path: Path) -> dict[str, Any]:
    """Reauthorize, then capture the new authorization (the D11 path); capture the other
    card's original authorization without reauthorizing (the ADR's fallback)."""
    state = _load(path)
    for holder in ("card_reauthorize", "paypal_account"):
        if "authorization_id" not in state.get(holder, {}):
            continue
        original: str = state[holder]["authorization_id"]

        async def reauthorize(key: str, auth: str = original) -> Authorization:
            return await client.reauthorize(auth, amount_minor=AMOUNT, request_id=key)

        renewed = await _step(state, path, f"{holder}-reauthorize", reauthorize)
        if isinstance(renewed, Authorization):
            state[holder]["reauthorized_id"] = renewed.authorization_id
            _save(path, state)
        if "reauthorized_id" in state[holder]:
            await _capture(
                client, state, path, f"{holder}-capture", state[holder]["reauthorized_id"]
            )
    await _capture(
        client,
        state,
        path,
        "card_capture_only-capture",
        state["card_capture_only"]["authorization_id"],
    )
    return state


async def _capture(
    client: PayPalClient, state: dict[str, Any], path: Path, step: str, authorization_id: str
) -> None:
    await _step(
        state,
        path,
        step,
        lambda key: client.capture(
            authorization_id,
            amount_minor=AMOUNT,
            invoice_id=_key(state, path, f"{step}-invoice"),
            request_id=key,
        ),
    )


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
            case ["reauth", "create", file]:
                return await reauth_create(client, Path(file))
            case ["reauth", "authorize", file]:
                return await reauth_authorize(client, Path(file))
            case ["reauth", "day4", file]:
                return await reauth_day4(client, Path(file))
            case _:
                raise SystemExit(__doc__)
    finally:
        await client.aclose()


if __name__ == "__main__":
    print(json.dumps({"ran_at": _now(), "report": asyncio.run(_main(sys.argv[1:]))}))
