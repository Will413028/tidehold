"""Sandbox checks run by hand against PayPal's sandbox (plan step 4 acceptance).

    python -m tidehold.bootstrap.sandbox smoke
        Needs DATABASE_URL (a throwaway database) and PAYPAL_ENV=sandbox credentials. Runs the
        three money flows through the money service: deposit capture then refund; balance
        hold then partial final capture; balance hold then void. Prints one JSON line.

    python -m tidehold.bootstrap.sandbox reauth create|authorize|day4 STATE_FILE
        The reauthorization check of the deposit/balance ADR. `create` (day 0) holds two
        card authorizations and creates one PayPal-account order whose approval link a
        person opens with a sandbox buyer; `authorize` authorizes that order once approved;
        `day4` (day 4 or later) reauthorizes the first card and the PayPal-account hold and
        captures the second card without reauthorizing. STATE_FILE keeps the IDs and request
        ids between days, outside any rebuilt database; request ids are written before each
        call, so a rerun after a crash repeats it instead of moving money twice.

Credentials come only from the environment (an --env-file); nothing here prints them.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from tidehold.money import commands, service
from tidehold.money.paypal import (
    TEST_CARD,
    PayPalClient,
    PayPalError,
    PayPalUnknownOutcome,
    authorization_of,
    order_id_of,
)
from tidehold.platform.db import Conn, connect
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import database_url_from_env, paypal_from_env

SMOKE_CAP = 10
AMOUNT = 3000  # USD 30.00


async def smoke(conn: Conn, client: PayPalClient) -> dict[str, Any]:
    row = await (
        await conn.execute(
            "insert into workspaces (session_token_hash)"
            " values (sha256(gen_random_uuid()::text::bytea)) returning id"
        )
    ).fetchone()
    if row is None:
        raise RuntimeError("no workspace row")
    workspace: UUID = row["id"]
    flows: dict[str, tuple[commands.Purpose, commands.Kind, int, bool]] = {
        "deposit_capture_then_refund": ("deposit", "refund", AMOUNT // 2, False),
        "balance_hold_then_partial_final_capture": ("balance", "capture", AMOUNT * 2 // 3, True),
        "balance_hold_then_void": ("balance", "void", 0, False),
    }
    report: dict[str, Any] = {}
    for name, (purpose, kind, amount, final) in flows.items():
        payment_id = await commands.create_payment(
            conn, workspace_id=workspace, purpose=purpose, amount_minor=AMOUNT
        )
        await service.pay_with_test_card(conn, client, payment_id, TEST_CARD, daily_cap=SMOKE_CAP)
        async with conn.transaction():
            command = await commands.record_command(
                conn, payment_id, kind, amount_minor=amount, final_capture=final
            )
        outcome = await service.execute(conn, client, command["id"])
        payment = await commands.payment(conn, payment_id)
        report[name] = {
            "outcome": outcome,
            "state": payment["state"],
            "captured_minor": payment["captured_minor"],
            "refunded_minor": payment["refunded_minor"],
            "authorization_id": payment["authorization_id"],
            "capture_id": payment["capture_id"],
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
                card=TEST_CARD,
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
        )
        order_id = order_id_of(order)
        state["paypal_account"] = {
            "order_id": order_id,
            "approve_url": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
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


async def _attempt(step: str, call: Any) -> dict[str, Any]:
    try:
        result = await call
    except PayPalError as error:
        return {"step": step, "at": _now(), "result": "refused", "code": error.code}
    except PayPalUnknownOutcome as error:
        return {"step": step, "at": _now(), "result": "unknown", "code": error.code}
    return {"step": step, "at": _now(), "result": "accepted", "detail": repr(result)}


async def reauth_day4(client: PayPalClient, path: Path) -> dict[str, Any]:
    state = _load(path)
    results = state.setdefault("day4", [])
    done = {r["step"] for r in results}
    for holder in ("card_reauthorize", "paypal_account"):
        step = f"{holder}-reauthorize"
        if step not in done and "authorization_id" in state.get(holder, {}):
            results.append(
                await _attempt(
                    step,
                    client.reauthorize(
                        state[holder]["authorization_id"],
                        amount_minor=AMOUNT,
                        request_id=_key(state, path, step),
                    ),
                )
            )
            _save(path, state)
    step = "card_capture_only-capture"
    if step not in done:
        results.append(
            await _attempt(
                step,
                client.capture(
                    state["card_capture_only"]["authorization_id"],
                    amount_minor=AMOUNT,
                    final=True,
                    invoice_id=_key(state, path, f"{step}-invoice"),
                    request_id=_key(state, path, step),
                ),
            )
        )
        _save(path, state)
    return state


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
