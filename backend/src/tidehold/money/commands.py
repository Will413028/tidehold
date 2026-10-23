"""Money command persistence. Ported from dive-trip-agent@8065eba
`modules/deposits/transactions.py` (its money parts), keyed by payment.

Callers own the transaction. A command is written before PayPal is called and completed
afterwards; `claim` marks it sent so no newer attempt can replace it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from tidehold.platform.db import Conn, insert_returning
from tidehold.platform.errors import Conflict, LimitReached, NotFound

Purpose = Literal["deposit", "balance"]
# What the payment's order asks PayPal for (D18): a capture at once, or (until plan step 8c) a
# hold on a balance created before D18.
Intent = Literal["capture", "authorize"]
Kind = Literal["open", "capture", "void", "reauthorize", "refund"]
Method = Literal["checkout", "test_card"]
Status = Literal["succeeded", "rejected", "unknown"]

# What each kind acts on, read from the payment when the command is recorded.
TARGET_OF: dict[Kind, str] = {
    "capture": "authorization_id",
    "void": "authorization_id",
    "reauthorize": "authorization_id",
    "refund": "capture_id",
}
# The payment state each hold command needs; a refund needs a captured payment of either
# purpose (D18, `record_refund`).
NEEDS_STATE: dict[Kind, tuple[Purpose, str]] = {
    "capture": ("balance", "authorized"),
    "void": ("balance", "authorized"),
    "reauthorize": ("balance", "authorized"),
}
# PayPal accepts a refund within 180 days of the capture (Checkout refunds, read 2026-10-23;
# D20). Counted from when the capture was first sent, which is never after PayPal's own time.
REFUND_WINDOW = timedelta(days=180)


def request_id(kind: Kind, command_id: UUID) -> str:
    return f"tidehold-{kind}-{command_id}"


def invoice_id(command_id: UUID) -> str:
    return f"tidehold-{command_id}"


async def append(conn: Conn, payment_id: UUID, kind: str, detail: dict[str, Any]) -> None:
    await conn.execute(
        "insert into payment_events (payment_id, kind, detail) values (%s, %s, %s)",
        (payment_id, kind, Jsonb(detail)),
    )


async def create_payment(
    conn: Conn, *, workspace_id: UUID, purpose: Purpose, intent: Intent, amount_minor: int
) -> UUID:
    row = await insert_returning(
        conn,
        "insert into payments (workspace_id, purpose, intent, amount_minor)"
        " values (%s, %s, %s, %s) returning id",
        (workspace_id, purpose, intent, amount_minor),
    )
    payment_id: UUID = row["id"]
    await append(conn, payment_id, "payment_created", {"purpose": purpose, "amount": amount_minor})
    return payment_id


async def payment(conn: Conn, payment_id: UUID, *, lock: bool = False) -> DictRow:
    query = (
        "select * from payments where id = %s for update"
        if lock
        else ("select * from payments where id = %s")
    )
    row = await (await conn.execute(query, (payment_id,))).fetchone()
    if row is None:
        raise NotFound
    return row


@dataclass(frozen=True)
class NewCommand:
    payment_id: UUID
    kind: Kind
    amount_minor: int
    method: Method | None = None
    target_id: str | None = None
    proposal_id: UUID | None = None


async def _insert(conn: Conn, new: NewCommand) -> DictRow:
    command_id = uuid4()
    return await insert_returning(
        conn,
        "insert into money_commands (id, payment_id, kind, method, target_id, amount_minor,"
        " request_id, invoice_id, status, proposal_id)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s) returning *",
        (
            command_id,
            new.payment_id,
            new.kind,
            new.method,
            new.target_id,
            new.amount_minor,
            request_id(new.kind, command_id),
            invoice_id(command_id),
            new.proposal_id,
        ),
    )


async def _within_daily_cap(conn: Conn, workspace_id: UUID, cap: int) -> None:
    """Count today's open attempts under a lock on the workspace row, so two concurrent
    attempts cannot both take the last slot."""
    await conn.execute("select 1 from workspaces where id = %s for update", (workspace_id,))
    row = await (
        await conn.execute(
            "select count(*) as n from money_commands c join payments p on p.id = c.payment_id"
            " where p.workspace_id = %s and c.kind = 'open'"
            " and c.created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'",
            (workspace_id,),
        )
    ).fetchone()
    if row is not None and row["n"] >= cap:
        raise LimitReached("PAYPAL_DAILY_CAP_REACHED")


async def open_attempt(conn: Conn, payment_id: UUID, method: Method, *, daily_cap: int) -> DictRow:
    """Record a new attempt to place a payment, inside the caller's transaction.

    A checkout the buyer never finished is replaced: PayPal may still show its order as
    approved, but only our capture or authorize call moves money, and that call now finds
    the attempt closed. An attempt already sent to PayPal blocks until it settles.
    """
    current = await payment(conn, payment_id, lock=True)
    if current["state"] != "pending":
        raise Conflict("PAYMENT_ALREADY_PLACED")
    await _within_daily_cap(conn, current["workspace_id"], daily_cap)
    replaced = await (
        await conn.execute(
            "update money_commands set status = 'rejected', error_code = 'CHECKOUT_REPLACED',"
            " completed_at = now() where payment_id = %s and kind = 'open'"
            " and status = 'pending' and sent_at is null returning id",
            (payment_id,),
        )
    ).fetchall()
    for row in replaced:
        await append(
            conn,
            payment_id,
            "open_rejected",
            {"command": str(row["id"]), "error": "CHECKOUT_REPLACED"},
        )
    if await (
        await conn.execute(
            "select 1 from money_commands where payment_id = %s and kind = 'open'"
            " and status in ('pending', 'unknown')",
            (payment_id,),
        )
    ).fetchone():
        raise Conflict("PAYMENT_ATTEMPT_IN_FLIGHT")
    return await _insert(
        conn, NewCommand(payment_id, "open", current["amount_minor"], method=method)
    )


async def set_order(conn: Conn, command_id: UUID, order_id: str) -> None:
    """Attach the checkout order to its attempt, unless the attempt was replaced meanwhile."""
    row = await (
        await conn.execute(
            "update money_commands set order_id = %s where id = %s and status = 'pending'"
            " and sent_at is null returning id",
            (order_id, command_id),
        )
    ).fetchone()
    if row is None:
        raise Conflict("PAYMENT_ATTEMPT_CLOSED")


async def checkout_attempt(conn: Conn, payment_id: UUID, order_id: str) -> UUID:
    row = await (
        await conn.execute(
            "select id from money_commands where payment_id = %s and kind = 'open'"
            " and method = 'checkout' and order_id = %s and status in ('pending', 'unknown')",
            (payment_id, order_id),
        )
    ).fetchone()
    if row is None:
        raise Conflict("PAYMENT_ATTEMPT_CLOSED")
    command_id: UUID = row["id"]
    return command_id


async def _check_refund(conn: Conn, current: DictRow, amount_minor: int, now: datetime) -> None:
    """A refund fits inside PayPal's window and what the payment captured, counting refunds
    still open. The caller holds the payment row, so a concurrent refund waits and then sees
    this one as open."""
    if amount_minor <= 0:
        raise ValueError("a refund returns a positive amount")
    if now > current["captured_at"] + REFUND_WINDOW:
        raise Conflict("REFUND_WINDOW_CLOSED")
    row = await (
        await conn.execute(
            "select coalesce(sum(amount_minor), 0) as n from money_commands"
            " where payment_id = %s and kind = 'refund' and status in ('pending', 'unknown')",
            (current["id"],),
        )
    ).fetchone()
    in_flight = int(row["n"]) if row else 0
    if amount_minor > current["captured_minor"] - current["refunded_minor"] - in_flight:
        raise Conflict("REFUND_EXCEEDS_CAPTURED")


async def record_command(
    conn: Conn,
    payment_id: UUID,
    kind: Kind,
    *,
    amount_minor: int,
    proposal_id: UUID | None = None,
) -> DictRow:
    """Record a follow-up command against what the payment placed, with the proposal that
    authorized it (step 8); the unique index refuses a second live command of the same step.
    A refund goes through `record_refund`, which needs the time for its window."""
    if kind in ("open", "refund"):
        raise ValueError(f"use {'open_attempt' if kind == 'open' else 'record_refund'}")
    current = await payment(conn, payment_id)
    purpose, state = NEEDS_STATE[kind]
    if (current["purpose"], current["state"]) != (purpose, state):
        raise Conflict(f"PAYMENT_NOT_{state.upper()}")
    return await _insert(
        conn,
        NewCommand(
            payment_id,
            kind,
            amount_minor,
            target_id=current[TARGET_OF[kind]],
            proposal_id=proposal_id,
        ),
    )


class RefundAlreadyRecorded(Conflict):
    """This settlement already has a live refund of this payment."""


async def record_refund(
    conn: Conn, payment_id: UUID, *, amount_minor: int, now: datetime, proposal_id: UUID | None
) -> DictRow:
    """Record a refund of a captured payment of either purpose (D18), under the payment's row
    lock: refused past PayPal's window and above what the capture still holds, counting
    refunds still open. One live refund per proposal and payment (unique index)."""
    current = await payment(conn, payment_id, lock=True)
    if current["state"] != "captured":
        raise Conflict("PAYMENT_NOT_CAPTURED")
    if (
        proposal_id is not None
        and await (
            await conn.execute(
                "select 1 from money_commands where proposal_id = %s and payment_id = %s"
                " and kind = 'refund' and status <> 'rejected'",
                (proposal_id, payment_id),
            )
        ).fetchone()
    ):
        # another run of the same settlement got the lock first; the unique index backs this
        raise RefundAlreadyRecorded("REFUND_ALREADY_RECORDED")
    await _check_refund(conn, current, amount_minor, now)
    kind: Kind = "refund"
    return await _insert(
        conn,
        NewCommand(
            payment_id,
            kind,
            amount_minor,
            target_id=current[TARGET_OF[kind]],
            proposal_id=proposal_id,
        ),
    )


# Refusals made here, before PayPal is called; a refund refused this way is stored `rejected`
# with `sent_at` null, so every later run of its settlement finds it final.
REFUSED_BEFORE_PAYPAL = ("REFUND_WINDOW_CLOSED", "REFUND_EXCEEDS_CAPTURED")


async def record_refused_refund(
    conn: Conn, payment_id: UUID, *, amount_minor: int, proposal_id: UUID, code: str
) -> DictRow:
    """Store a settlement's refund that was refused before PayPal (`REFUSED_BEFORE_PAYPAL`):
    closed at once, never sent, so a rerun does not try it again and the operator is asked
    with the reason (plan step 8b)."""
    current = await payment(conn, payment_id)
    command_id = uuid4()
    row = await insert_returning(
        conn,
        "insert into money_commands (id, payment_id, kind, target_id, amount_minor, request_id,"
        " invoice_id, status, error_code, completed_at, proposal_id)"
        " values (%s, %s, 'refund', %s, %s, %s, %s, 'rejected', %s, now(), %s) returning *",
        (
            command_id,
            payment_id,
            current["capture_id"],
            amount_minor,
            request_id("refund", command_id),
            invoice_id(command_id),
            code,
            proposal_id,
        ),
    )
    await append(conn, payment_id, "refund_refused", {"command": str(command_id), "error": code})
    return row


async def close_refund_past_window(conn: Conn, command_id: UUID) -> bool:
    """Close a refund never sent whose payment's window has passed, without calling PayPal.
    PayPal's window runs on real time, so this reads the database clock at the moment of the
    first send (recording checked the caller's clock). The row lock and `sent_at is null` keep
    it from racing a send."""
    row = await (
        await conn.execute(
            "update money_commands c set status = 'rejected', error_code = 'REFUND_WINDOW_CLOSED',"
            " completed_at = now() from payments p"
            " where c.id = %s and p.id = c.payment_id and c.kind = 'refund'"
            " and c.status = 'pending' and c.sent_at is null"
            " and now() > p.captured_at + make_interval(days => %s)"
            " returning c.payment_id",
            (command_id, REFUND_WINDOW.days),
        )
    ).fetchone()
    if row is None:
        return False
    await append(
        conn,
        row["payment_id"],
        "refund_refused",
        {"command": str(command_id), "error": "REFUND_WINDOW_CLOSED"},
    )
    return True


async def claim(conn: Conn, command_id: UUID, *, first_send: bool = False) -> DictRow:
    """Mark an open command as sent, so no newer attempt can replace it. `resend` says
    whether it went to PayPal before, whose answer then cannot prove that no money moved.
    With `first_send`, a command someone already sent is refused (`MONEY_COMMAND_ALREADY_SENT`):
    only reconciliation resends, after reading PayPal (D18 D4)."""
    if first_send:
        prior = await (
            await conn.execute(
                "select sent_at from money_commands where id = %s for update", (command_id,)
            )
        ).fetchone()
        if prior is not None and prior["sent_at"] is not None:
            raise Conflict("MONEY_COMMAND_ALREADY_SENT")
    row = await (
        await conn.execute(
            "with prior as (select id, sent_at from money_commands where id = %s for update)"
            " update money_commands c set sent_at = coalesce(c.sent_at, now()) from prior"
            " where c.id = prior.id and c.status in ('pending', 'unknown')"
            " returning c.*, prior.sent_at is not null as resend",
            (command_id,),
        )
    ).fetchone()
    if row is None:
        raise Conflict("MONEY_COMMAND_NOT_OPEN")
    return row


async def mark_resend_refused(conn: Conn, command_id: UUID) -> None:
    """PayPal refused a resend: the first send may have moved money, so the command stays open
    with its key, and is not resent again once a read cannot settle it (D18 D4)."""
    await conn.execute(
        "update money_commands set resend_refused_at = coalesce(resend_refused_at, now())"
        " where id = %s",
        (command_id,),
    )


async def open_commands(conn: Conn, payment_id: UUID) -> list[UUID]:
    rows = await (
        await conn.execute(
            "select id from money_commands where payment_id = %s"
            " and status in ('pending', 'unknown') order by created_at, id",
            (payment_id,),
        )
    ).fetchall()
    return [row["id"] for row in rows]


async def complete(
    conn: Conn,
    command_id: UUID,
    status: Status,
    *,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> DictRow:
    """Settle a command and apply a succeeded outcome to its payment. PayPal's last answer is
    kept when this call has none (a pending refund's id survives a later unknown); an `open`
    keeps the order its answer names."""
    order = (result or {}).get("orderId")
    row = await (
        await conn.execute(
            "update money_commands set status = %s, result = coalesce(%s, result),"
            " error_code = %s, attempts = attempts + 1,"
            " order_id = case when kind = 'open' then coalesce(order_id, %s) end,"
            " completed_at = case when %s in ('succeeded', 'rejected') then now() end"
            " where id = %s and status in ('pending', 'unknown') returning *",
            (
                status,
                Jsonb(result) if result is not None else None,
                error_code,
                order,
                status,
                command_id,
            ),
        )
    ).fetchone()
    if row is None:
        raise Conflict("MONEY_COMMAND_NOT_OPEN")
    if status == "succeeded":
        await _apply(conn, row, result or {})
    await append(
        conn,
        row["payment_id"],
        f"{row['kind']}_{status}",
        {"command": str(command_id), "amount": row["amount_minor"], "error": error_code},
    )
    return row


async def _apply(conn: Conn, command: DictRow, result: dict[str, Any]) -> None:
    payment_id, amount = command["payment_id"], command["amount_minor"]
    match command["kind"]:
        case "open" if "captureId" in result:
            await conn.execute(
                "update payments set state = 'captured', capture_id = %s, captured_minor = %s,"
                " captured_at = %s, order_id = %s where id = %s",
                (result["captureId"], amount, command["sent_at"], result["orderId"], payment_id),
            )
        case "open":
            await conn.execute(
                "update payments set state = 'authorized', authorization_id = %s,"
                " authorization_expires_at = %s, order_id = %s where id = %s",
                (result["authorizationId"], result.get("expiresAt"), result["orderId"], payment_id),
            )
        case "reauthorize":
            await conn.execute(
                "update payments set state = 'authorized', authorization_id = %s,"
                " authorization_expires_at = %s where id = %s",
                (result["authorizationId"], result.get("expiresAt"), payment_id),
            )
        case "capture":
            await conn.execute(
                "update payments set state = 'captured', capture_id = %s, captured_minor = %s,"
                " captured_at = %s where id = %s",
                (result["captureId"], amount, command["sent_at"], payment_id),
            )
        case "void":
            await conn.execute("update payments set state = 'voided' where id = %s", (payment_id,))
        case "refund":
            await conn.execute(
                "update payments set refunded_minor = refunded_minor + %s where id = %s",
                (amount, payment_id),
            )
        case kind:
            raise ValueError(f"unknown command kind {kind}")
