"""Money command execution: store, call PayPal outside any transaction, then settle.

Ported from dive-trip-agent@8065eba `modules/deposits/service.py`. A command commits as
`pending` before PayPal is called. A refusal of its first send closes it; an unknown outcome,
or a refusal of a resend, leaves it open,
and `reconcile` resends it with the same request id so PayPal returns the original result
instead of moving money again. Who calls `reconcile` and when: the due action of plan step 6
and the nightly job of step 10.
"""

from typing import Any, Literal
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.money import commands
from tidehold.money.paypal import (
    PayPalClient,
    PayPalError,
    PayPalUnexpectedResponse,
    PayPalUnknownOutcome,
    SandboxCard,
    authorization_of,
    capture_of,
    order_id_of,
)
from tidehold.platform.db import Conn
from tidehold.platform.errors import Conflict, DomainError

Outcome = Literal["succeeded", "rejected", "unknown"]


async def _open(
    client: PayPalClient, command: DictRow, payment: DictRow, card: SandboxCard | None
) -> dict[str, Any]:
    deposit = payment["purpose"] == "deposit"
    if command["method"] == "checkout":
        if command["order_id"] is None:
            raise Conflict("PAYMENT_ORDER_MISSING")
        order = await (
            client.capture_order(command["order_id"], request_id=command["request_id"])
            if deposit
            else client.authorize_order(command["order_id"], request_id=command["request_id"])
        )
    elif card is not None:
        order = await client.create_order(
            intent="CAPTURE" if deposit else "AUTHORIZE",
            invoice_id=command["invoice_id"],
            amount_minor=command["amount_minor"],
            request_id=command["request_id"],
            card=card,
        )
    else:
        raise Conflict("PAYMENT_CARD_REQUIRED")
    if deposit:
        capture = capture_of(order)
        if capture.status != "COMPLETED" or capture.amount_minor != command["amount_minor"]:
            raise PayPalUnexpectedResponse("PAYPAL_CAPTURE_UNEXPECTED")
        return {"orderId": order_id_of(order), "captureId": capture.capture_id}
    auth = authorization_of(order)
    if auth.status != "CREATED" or auth.amount_minor != command["amount_minor"]:
        raise PayPalUnexpectedResponse("PAYPAL_AUTHORIZATION_UNEXPECTED")
    return {
        "orderId": order_id_of(order),
        "authorizationId": auth.authorization_id,
        "expiresAt": auth.expires_at,
    }


async def send(
    client: PayPalClient, command: DictRow, payment: DictRow, card: SandboxCard | None
) -> dict[str, Any]:
    target, key, amount = command["target_id"], command["request_id"], command["amount_minor"]
    match command["kind"]:
        case "open":
            return await _open(client, command, payment, card)
        case "capture":
            capture = await client.capture(
                target,
                amount_minor=amount,
                invoice_id=command["invoice_id"],
                request_id=key,
            )
            if capture.amount_minor != amount:
                raise PayPalUnexpectedResponse("PAYPAL_CAPTURE_UNEXPECTED")
            return {"captureId": capture.capture_id, "status": capture.status}
        case "void":
            await client.void(target, request_id=key)
            return {}
        case "reauthorize":
            auth = await client.reauthorize(target, amount_minor=amount, request_id=key)
            if auth.status != "CREATED":
                raise PayPalUnexpectedResponse("PAYPAL_REAUTHORIZATION_UNEXPECTED")
            return {"authorizationId": auth.authorization_id, "expiresAt": auth.expires_at}
        case "refund":
            refund = await client.refund(
                target, amount_minor=amount, invoice_id=command["invoice_id"], request_id=key
            )
            if refund.amount_minor != amount:
                raise PayPalUnexpectedResponse("PAYPAL_REFUND_UNEXPECTED")
            return {"refundId": refund.refund_id, "status": refund.status}
        case kind:
            raise ValueError(f"unknown command kind {kind}")


async def execute(
    conn: Conn, client: PayPalClient, command_id: UUID, card: SandboxCard | None = None
) -> Outcome:
    async with conn.transaction():
        command = await commands.claim(conn, command_id)
        if command["method"] == "test_card" and card is None:
            raise Conflict("PAYMENT_CARD_REQUIRED")  # rolls the claim back
        payment = await commands.payment(conn, command["payment_id"])
    try:
        result = await send(client, command, payment, card)
    except PayPalUnknownOutcome as error:
        async with conn.transaction():
            await commands.complete(conn, command_id, "unknown", error_code=error.code)
        return "unknown"
    except PayPalError as error:
        # A refusal of the first send means no money moved. A refusal of a resend may mean
        # the first send took effect (the request id expired, or PayPal says the hold is
        # already captured), so the command stays open and keeps its key; step 8 turns a
        # command that stays open into an operator ask.
        status: commands.Status = "unknown" if command["resend"] else "rejected"
        async with conn.transaction():
            await commands.complete(conn, command_id, status, error_code=error.code)
        return status
    async with conn.transaction():
        await commands.complete(conn, command_id, "succeeded", result=result)
    return "succeeded"


async def reconcile(
    conn: Conn, client: PayPalClient, payment_id: UUID, card: SandboxCard | None = None
) -> dict[UUID, str]:
    """Resend every open command of a payment with its original request id. One command that
    cannot be sent does not stop the others; its code is the outcome."""
    outcomes: dict[UUID, str] = {}
    for command_id in await commands.open_commands(conn, payment_id):
        try:
            outcomes[command_id] = await execute(conn, client, command_id, card)
        except DomainError as error:
            outcomes[command_id] = error.code
    return outcomes


class PaymentRefused(Conflict):
    """The attempt closed without placing the payment; the code says why."""


async def _placed(conn: Conn, command_id: UUID, outcome: Outcome) -> None:
    if outcome == "unknown":
        raise PayPalUnknownOutcome
    if outcome == "rejected":
        row = await (
            await conn.execute("select error_code from money_commands where id = %s", (command_id,))
        ).fetchone()
        raise PaymentRefused((row or {}).get("error_code") or "PAYPAL_REJECTED")


async def pay_with_test_card(
    conn: Conn, client: PayPalClient, payment_id: UUID, card: SandboxCard, *, daily_cap: int
) -> None:
    """Place the payment with the server's sandbox test card (the fallback when PayPal's
    checkout is unavailable in the sandbox)."""
    async with conn.transaction():
        command = await commands.open_attempt(conn, payment_id, "test_card", daily_cap=daily_cap)
    await _placed(conn, command["id"], await execute(conn, client, command["id"], card))


async def start_checkout(
    conn: Conn, client: PayPalClient, payment_id: UUID, *, daily_cap: int
) -> str:
    """Create the order the buyer approves in PayPal's checkout and return its id.

    The attempt is recorded first, so the daily cap counts it before PayPal is called.
    Creating an order moves no money; an order whose attempt is replaced is never captured
    or authorized and expires at PayPal.
    """
    async with conn.transaction():
        command = await commands.open_attempt(conn, payment_id, "checkout", daily_cap=daily_cap)
        payment = await commands.payment(conn, payment_id)
    try:
        order = await client.create_order(
            intent="CAPTURE" if payment["purpose"] == "deposit" else "AUTHORIZE",
            invoice_id=command["invoice_id"],
            amount_minor=command["amount_minor"],
            request_id=f"{command['request_id']}-order",
        )
        order_id = order_id_of(order)
    except PayPalError as error:
        async with conn.transaction():
            await commands.complete(conn, command["id"], "rejected", error_code=error.code)
        raise
    # On an unknown outcome the attempt stays unsent without an order, so the next attempt
    # replaces it; an order PayPal may have created moves no money.
    async with conn.transaction():
        await commands.set_order(conn, command["id"], order_id)
    return order_id


async def complete_checkout(
    conn: Conn, client: PayPalClient, payment_id: UUID, order_id: str
) -> None:
    async with conn.transaction():
        command_id = await commands.checkout_attempt(conn, payment_id, order_id)
    try:
        outcome = await execute(conn, client, command_id)
    except Conflict as error:
        if error.code != "MONEY_COMMAND_NOT_OPEN":
            raise
        # Closed meanwhile: by a reconcile that placed this payment, or by a newer attempt.
        row = await (
            await conn.execute("select status from money_commands where id = %s", (command_id,))
        ).fetchone()
        if row is None or row["status"] != "succeeded":
            raise Conflict("PAYMENT_ATTEMPT_CLOSED") from error
        outcome = "succeeded"
    await _placed(conn, command_id, outcome)
