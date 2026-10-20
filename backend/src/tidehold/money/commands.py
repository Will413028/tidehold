"""Money command persistence. Ported from dive-trip-agent@8065eba
`modules/deposits/transactions.py` (its money parts), keyed by payment.

Callers own the transaction. A command is written before PayPal is called and completed
afterwards; `claim` marks it sent so no newer attempt can replace it.
"""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from tidehold.platform.db import Conn, insert_returning
from tidehold.platform.errors import Conflict, LimitReached, NotFound

Purpose = Literal["deposit", "balance"]
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
# The payment state each follow-up command needs.
NEEDS_STATE: dict[Kind, tuple[Purpose, str]] = {
    "capture": ("balance", "authorized"),
    "void": ("balance", "authorized"),
    "reauthorize": ("balance", "authorized"),
    "refund": ("deposit", "captured"),
}


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
    conn: Conn, *, workspace_id: UUID, purpose: Purpose, amount_minor: int
) -> UUID:
    row = await insert_returning(
        conn,
        "insert into payments (workspace_id, purpose, amount_minor) values (%s, %s, %s)"
        " returning id",
        (workspace_id, purpose, amount_minor),
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


async def record_command(
    conn: Conn,
    payment_id: UUID,
    kind: Kind,
    *,
    amount_minor: int,
    proposal_id: UUID | None = None,
) -> DictRow:
    """Record a follow-up command against what the payment placed, with the proposal that
    authorized it (step 8); the unique index refuses a second live command of the same step."""
    if kind == "open":
        raise ValueError("use open_attempt")
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


async def claim(conn: Conn, command_id: UUID) -> DictRow:
    """Mark an open command as sent, so no newer attempt can replace it. `resend` says
    whether it went to PayPal before, whose answer then cannot prove that no money moved."""
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
    """Settle a command and apply a succeeded outcome to its payment."""
    row = await (
        await conn.execute(
            "update money_commands set status = %s, result = %s, error_code = %s,"
            " attempts = attempts + 1,"
            " completed_at = case when %s in ('succeeded', 'rejected') then now() end"
            " where id = %s and status in ('pending', 'unknown') returning *",
            (status, Jsonb(result) if result is not None else None, error_code, status, command_id),
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
                "update payments set state = 'captured', capture_id = %s, captured_minor = %s"
                " where id = %s",
                (result["captureId"], amount, payment_id),
            )
        case "open" | "reauthorize":
            await conn.execute(
                "update payments set state = 'authorized', authorization_id = %s,"
                " authorization_expires_at = %s where id = %s",
                (result["authorizationId"], result.get("expiresAt"), payment_id),
            )
        case "capture":
            await conn.execute(
                "update payments set state = 'captured', capture_id = %s, captured_minor = %s"
                " where id = %s",
                (result["captureId"], amount, payment_id),
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
