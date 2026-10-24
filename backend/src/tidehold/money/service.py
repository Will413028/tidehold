"""Money command execution: store, call PayPal outside any transaction, then settle.

Ported from dive-trip-agent@8065eba `modules/deposits/service.py`. A command commits as
`pending` before PayPal is called. A refusal of its first send closes it; an unknown outcome,
or a refusal of a resend, leaves it open. `reconcile` first reads PayPal's own record of an
open payment or a refund (D18 D4) and settles the command from it; only what the read shows
not done is resent, with the same request id, so PayPal returns the original result instead
of moving money again. Who calls `reconcile` and when: the due action of plan step 6 and the
nightly job of step 10.
"""

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.money import commands
from tidehold.money.paypal import (
    TEST_CARD,
    PayPalClient,
    PayPalError,
    PayPalUnexpectedResponse,
    PayPalUnknownOutcome,
    Refund,
    SandboxCard,
    capture_of,
    order_id_of,
    refunds_of,
)
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn
from tidehold.platform.errors import Conflict, DomainError

Outcome = Literal["succeeded", "rejected", "unknown"]
# What a read tells reconciliation: an outcome; `pending`, PayPal still processing a refund
# (not a failure: it is read again until it ends, D18 D4); or `held`, a command PayPal refused
# to resend that no read settles, left for the operator (step 8b), not a failed read.
ReadOutcome = Literal["succeeded", "rejected", "unknown", "pending", "held"]
# How often a refund PayPal reports PENDING is read again. Such a refund can stay pending for
# days (an eCheck payment, per the developer's review of 2026-10-23; PayPal's refund status
# reference could not be read from the build container, so the duration is unverified), and the
# deferred webhook (plan, Scope) would replace these reads.
PENDING_REFUND_READ_EVERY = timedelta(hours=1)


class OutcomePending(PayPalUnknownOutcome):
    """PayPal answered, but the money has not settled (or the answer was not one we can
    record); what it did tell us (an order id, a refund id) is kept for the read that follows."""

    def __init__(self, code: str, result: dict[str, Any]) -> None:
        super().__init__(code)
        self.result = result


def _placed_by(order: dict[str, Any], command: DictRow) -> dict[str, Any]:
    """What an order that placed the payment shows, in the shape `_apply` records. Anything
    short of a completed capture of exactly the command's amount stays unknown; the order id is
    kept so the next read can find it."""
    order_id = order.get("id")
    try:
        found = capture_of(order)
        if found.status == "COMPLETED" and found.amount_minor == command["amount_minor"]:
            return {"orderId": order_id_of(order), "captureId": found.capture_id}
        code = "PAYPAL_CAPTURE_UNEXPECTED"
    except PayPalUnexpectedResponse as error:
        code = error.code
    raise OutcomePending(code, {"orderId": str(order_id)} if order_id else {})


async def _open(client: PayPalClient, command: DictRow, card: SandboxCard | None) -> dict[str, Any]:
    if command["method"] == "checkout":
        if command["order_id"] is None:
            raise Conflict("PAYMENT_ORDER_MISSING")
        order = await client.capture_order(command["order_id"], request_id=command["request_id"])
    elif card is not None:
        order = await client.create_order(
            invoice_id=command["invoice_id"],
            amount_minor=command["amount_minor"],
            request_id=command["request_id"],
            source=card,
        )
    else:
        raise Conflict("PAYMENT_CARD_REQUIRED")
    return _placed_by(order, command)


async def _refund(client: PayPalClient, command: DictRow) -> dict[str, Any]:
    refund = await client.refund(
        command["target_id"],
        amount_minor=command["amount_minor"],
        invoice_id=command["invoice_id"],
        request_id=command["request_id"],
    )
    result = {"refundId": refund.refund_id, "status": refund.status}
    if refund.status == "PENDING":
        # the money leaves the merchant later; a read settles it (D18 D4)
        raise OutcomePending("PAYPAL_REFUND_PENDING", result)
    if refund.status != "COMPLETED" or refund.amount_minor != command["amount_minor"]:
        raise OutcomePending("PAYPAL_REFUND_UNEXPECTED", result)
    return result


async def send(client: PayPalClient, command: DictRow, card: SandboxCard | None) -> dict[str, Any]:
    match command["kind"]:
        case "open":
            return await _open(client, command, card)
        case "refund":
            return await _refund(client, command)
        case kind:
            raise ValueError(f"unknown command kind {kind}")


async def execute(
    conn: Conn,
    client: PayPalClient,
    command_id: UUID,
    card: SandboxCard | None = None,
    *,
    first_send: bool = False,
) -> Outcome:
    async with conn.transaction():
        if await commands.close_refund_past_window(conn, command_id):
            return "rejected"  # never sent, and PayPal would refuse it now
    async with conn.transaction():
        command = await commands.claim(conn, command_id, first_send=first_send)
        if command["method"] == "test_card" and card is None:
            raise Conflict("PAYMENT_CARD_REQUIRED")  # rolls the claim back
    try:
        result = await send(client, command, card)
    except PayPalUnknownOutcome as error:
        async with conn.transaction():
            await commands.complete(
                conn,
                command_id,
                "unknown",
                result=error.result if isinstance(error, OutcomePending) else None,
                error_code=error.code,
            )
            await due.schedule(conn, "reconcile_payment", command["payment_id"], due_at=None)
        return "unknown"
    except PayPalError as error:
        # A refusal of the first send means no money moved. A refusal of a resend may mean
        # the first send took effect (the request id expired, or PayPal says the order is
        # already captured), so the command stays open, keeps its key and is marked; a read
        # settles it if it can, and step 8b asks the operator when it cannot.
        status: commands.Status = "unknown" if command["resend"] else "rejected"
        async with conn.transaction():
            await commands.complete(conn, command_id, status, error_code=error.code)
            if command["resend"]:
                await commands.mark_resend_refused(conn, command_id)
        return status
    async with conn.transaction():
        await commands.complete(conn, command_id, "succeeded", result=result)
    return "succeeded"


# What a read of PayPal says about a command: settled either way, still undecided (nothing is
# sent or recorded), not done at PayPal (send it again under its key), or held: not done as far
# as the read shows, but PayPal refused its resend already (the operator is asked, step 8b).
Read = Literal["succeeded", "rejected", "undecided", "resend", "held"]
REFUND_SETTLED: dict[str, Read] = {
    "COMPLETED": "succeeded",
    "FAILED": "rejected",
    "CANCELLED": "rejected",
    "PENDING": "undecided",
}


async def _read_open(client: PayPalClient, command: DictRow) -> tuple[Read, dict[str, Any] | None]:
    order_id = command["order_id"] or (command["result"] or {}).get("orderId")
    if order_id is None:
        return "resend", None  # PayPal never told us an order: only the key can find it
    try:
        order = await client.get_order(order_id)
    except PayPalError as error:
        # an order PayPal does not show is not proof that nothing was captured: resend under
        # the key (plan step 4), and a refusal of that resend marks it for the operator
        return ("resend", None) if error.status == 404 else ("undecided", None)
    try:
        return "succeeded", _placed_by(order, command)
    except OutcomePending:
        pass
    return _not_placed(order, command), None


def _not_placed(order: dict[str, Any], command: DictRow) -> Read:
    """An order that shows no capture as recorded."""
    payments = ((order.get("purchase_units") or [{}])[0].get("payments")) or {}
    if payments.get("captures"):
        return "undecided"  # placed, but not as recorded (the amount, or still pending)
    status = order.get("status")
    if status == "APPROVED" and command["method"] == "checkout":
        return "resend"  # the buyer approved; our capture never landed
    if status in ("CREATED", "PAYER_ACTION_REQUIRED", "VOIDED"):
        # nothing placed, and nothing will be without the buyer: closing it lets the guest
        # start a new attempt (a resend would only be refused and keep the attempt open)
        return "rejected"
    return "undecided"


def _matching(refund: Refund, command: DictRow) -> tuple[Read, dict[str, Any]]:
    result = {"refundId": refund.refund_id, "status": refund.status}
    read = REFUND_SETTLED.get(refund.status, "undecided")
    if read == "succeeded" and refund.amount_minor != command["amount_minor"]:
        read = "undecided"
    return read, result


async def _read_refund(
    client: PayPalClient, command: DictRow, payment: DictRow
) -> tuple[Read, dict[str, Any] | None]:
    refund_id = (command["result"] or {}).get("refundId")
    try:
        if refund_id is not None:
            return _matching(await client.get_refund(refund_id), command)
        if payment["order_id"] is None:
            return "resend", None
        refunds = refunds_of(await client.get_order(payment["order_id"]))
    except (PayPalError, PayPalUnknownOutcome):
        return "undecided", None  # a read that failed proves nothing either way
    found = [r for r in refunds if r.invoice_id == command["invoice_id"]]
    if not found:
        return "resend", None
    return _matching(found[0], command)


async def _read(
    client: PayPalClient, command: DictRow, payment: DictRow
) -> tuple[Read, dict[str, Any] | None]:
    """Read PayPal's record of a command whose outcome we do not know; one never sent is sent."""
    unknown = command["status"] == "unknown" or command["sent_at"] is not None
    try:
        match command["kind"]:
            case "open" if unknown:
                read, result = await _read_open(client, command)
            case "refund" if unknown:
                read, result = await _read_refund(client, command, payment)
            case _:
                read, result = "resend", None
    except PayPalUnknownOutcome:
        return "undecided", None
    if read == "resend" and command["resend_refused_at"] is not None:
        return "held", None  # PayPal refused it once already; the operator decides (8b)
    return read, result


async def _settle_from_read(
    conn: Conn, command: DictRow, read: Read, result: dict[str, Any] | None
) -> ReadOutcome:
    async with conn.transaction():
        if read in ("succeeded", "rejected"):
            status: commands.Status = "succeeded" if read == "succeeded" else "rejected"
            # a refusal keeps what PayPal said (a refund's FAILED or CANCELLED) for the ask
            reason = (
                f"PAYPAL_{command['kind'].upper()}_{(result or {}).get('status', 'NOT_PLACED')}"
            )
            await commands.complete(
                conn,
                command["id"],
                status,
                result=result,
                error_code=None if read == "succeeded" else reason,
            )
            if command["proposal_id"] is not None:
                # the settlement that sent it may have given up waiting; it finds this command
                # done and finishes (plan step 8a's handler, rerun safely)
                await due.schedule(conn, "settle_proposal", command["proposal_id"], due_at=None)
            return status
        if result is not None and result != command["result"]:
            # keep what PayPal said (a refund id), once: an unchanged read writes nothing
            await commands.complete(
                conn, command["id"], "unknown", result=result, error_code=command["error_code"]
            )
    if read == "held":
        return "held"
    return "pending" if (result or {}).get("status") == "PENDING" else "unknown"


async def reconcile(
    conn: Conn, client: PayPalClient, payment_id: UUID, card: SandboxCard | None = None
) -> dict[UUID, str]:
    """Settle every open command of a payment: a command that may have reached PayPal is read
    first, and only one PayPal shows not done is resent with its original request id. One
    command that cannot be sent does not stop the others; its code is the outcome."""
    outcomes: dict[UUID, str] = {}
    payment = await commands.payment(conn, payment_id)
    for command_id in await commands.open_commands(conn, payment_id):
        command = await (
            await conn.execute("select * from money_commands where id = %s", (command_id,))
        ).fetchone()
        if command is None:
            continue
        read, result = await _read(client, command, payment)
        if command["sent_at"] is not None:
            await commands.mark_read(conn, command_id)
        try:
            outcomes[command_id] = await (
                # a command never sent is sent as its first send: if someone sent it since this
                # read its row, it is left to the next run, which reads PayPal first
                execute(conn, client, command_id, card, first_send=command["sent_at"] is None)
                if read == "resend"
                else _settle_from_read(conn, command, read, result)
            )
        except Conflict as error:
            if error.code != "MONEY_COMMAND_ALREADY_SENT":
                outcomes[command_id] = error.code  # completed meanwhile, or not sendable
                continue
            outcomes[command_id] = await _sent_since(conn, command_id)
        except DomainError as error:  # not sendable: the others go on
            outcomes[command_id] = error.code
    return outcomes


async def _sent_since(conn: Conn, command_id: UUID) -> str:
    """A command someone else sent while reconciliation was about to: its outcome if it has
    one, `pending` if PayPal is processing it, else `unknown`, so the action runs again and
    reads PayPal first."""
    row = await (
        await conn.execute("select status, result from money_commands where id = %s", (command_id,))
    ).fetchone()
    if row is None or row["status"] == "pending":
        return "unknown"
    if row["status"] == "unknown" and (row["result"] or {}).get("status") == "PENDING":
        return "pending"  # PayPal is processing it: waiting, not a failure
    return str(row["status"])


async def reconcile_due(conn: Conn, client: PayPalClient, payment_id: UUID) -> None:
    """The `reconcile_payment` due action: settle the payment's open commands. While an outcome
    is unknown (a read or a send that failed) it backs off and, after the failures in a row
    `due_actions` allows, fails for the operator; a refund PayPal reports PENDING is only
    waiting, so it is read again every `PENDING_REFUND_READ_EVERY` until it ends. A command held
    for the operator (PayPal refused its resend, no read settles it) is not retried: the
    settlement's `command_open` ask (plan step 8b) takes it to the operator."""
    outcomes = await reconcile(conn, client, payment_id, TEST_CARD)
    if "unknown" in outcomes.values():
        raise due.RetryLater("PAYPAL_OUTCOME_STILL_UNKNOWN")
    if "pending" in outcomes.values():
        raise due.Waiting(PENDING_REFUND_READ_EVERY, "PAYPAL_REFUND_PENDING")


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
    and expires at PayPal.
    """
    async with conn.transaction():
        command = await commands.open_attempt(conn, payment_id, "checkout", daily_cap=daily_cap)
    try:
        order = await client.create_order(
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
