"""The `settle_proposal` due action: move the money an accepted whole-trip settlement decides
now (plan step 8a). Deposit refunds are sent, and a balance that owes nothing is voided; a
balance that still owes a capture, and the money of day cancellations, wait for finalize
(step 8b). Each command records the proposal that authorized it.

The handler re-reads the proposal before acting (a cancellation or decision since it was
scheduled supersedes it by state, D17), and a rerun finds the commands an earlier run stored,
so it resumes instead of sending twice.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import DictRow

from tidehold.money import commands, service
from tidehold.money.amounts import usd
from tidehold.money.paypal import PayPalClient
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn
from tidehold.platform.errors import Conflict
from tidehold.settlement import proposals
from tidehold.settlement.plan import Payment, settle_payment
from tidehold.trips import store as trips


async def _command(conn: Conn, payment_id: UUID, kind: str, proposal_id: UUID) -> DictRow | None:
    row: DictRow | None = await (
        await conn.execute(
            "select id, status, sent_at, error_code, result, amount_minor from money_commands"
            " where payment_id = %s and kind = %s"
            " and proposal_id = %s order by created_at desc limit 1",
            (payment_id, kind, proposal_id),
        )
    ).fetchone()
    return row


async def _record(
    conn: Conn, proposal: DictRow, payment_id: UUID, move: dict[str, Any], now: datetime
) -> DictRow | None:
    """Store the command, or None when a concurrent run of this settlement stored it first (the
    unique indexes keep one live command per step, and one live refund per proposal). A refund
    refused before PayPal is stored closed, so this guest's move ends there and the others go
    on."""
    try:
        async with conn.transaction():
            if move["kind"] == "refund":
                return await commands.record_refund(
                    conn,
                    payment_id,
                    amount_minor=move["amount_minor"],
                    now=now,
                    proposal_id=proposal["id"],
                )
            return await commands.record_command(
                conn,
                payment_id,
                move["kind"],
                amount_minor=move["amount_minor"],
                proposal_id=proposal["id"],
            )
    except (psycopg.errors.UniqueViolation, commands.RefundAlreadyRecorded):
        return None
    except Conflict as error:
        if error.code not in commands.REFUSED_BEFORE_PAYPAL:
            raise
        code = error.code
    async with conn.transaction():
        return await commands.record_refused_refund(
            conn,
            payment_id,
            amount_minor=move["amount_minor"],
            proposal_id=proposal["id"],
            code=code,
        )


async def _send(
    conn: Conn,
    client: PayPalClient,
    proposal: DictRow,
    step: tuple[UUID, dict[str, Any]],
    now: datetime,
) -> str:
    """Send one money command for the proposal, or resume the one already stored; returns
    its status. A command already sent whose answer is not recorded is left to
    `reconcile_payment`, which reads PayPal before sending anything again (D18 D4)."""
    payment_id, move = step
    existing = await _command(conn, payment_id, move["kind"], proposal["id"])
    if existing is None:
        existing = await _record(conn, proposal, payment_id, move, now)
        if existing is None:
            # another run of this settlement stored it and sends it; this run waits for its
            # outcome (or for `reconcile_payment`, should that run stop before sending)
            stored = await _command(conn, payment_id, move["kind"], proposal["id"])
            return "pending" if stored is None else _status(stored)
    if existing["status"] == "pending":
        try:
            # only the first send goes from here: a concurrent run of this settlement may be
            # sending it right now, and a send whose answer was lost is resent only after a read
            await service.execute(conn, client, existing["id"], first_send=True)
        except Conflict as error:
            if error.code not in ("MONEY_COMMAND_ALREADY_SENT", "MONEY_COMMAND_NOT_OPEN"):
                raise
            async with conn.transaction():
                await due.schedule(conn, "reconcile_payment", payment_id, due_at=None)
        existing = await _command(conn, payment_id, move["kind"], proposal["id"]) or existing
    return _status(existing)


async def _being_read(conn: Conn, payment_id: UUID) -> bool:
    """Whether a `reconcile_payment` reads the payment's open command, scheduling one when the
    last ended (its wake can be dropped while it runs); False once the reads gave up (failed),
    which the operator console shows."""
    last = await (
        await conn.execute(
            "select state from due_actions where kind = 'reconcile_payment' and payment_id = %s"
            " order by id desc limit 1",
            (payment_id,),
        )
    ).fetchone()
    if last is not None and last["state"] == "failed":
        return False
    if last is None or last["state"] == "done":
        await due.schedule(conn, "reconcile_payment", payment_id, due_at=None)
    return True


def _status(command: DictRow) -> str:
    """What one move's command means for its settlement:
    - `refused`: closed before PayPal (window, bound), never sent; the operator decides;
    - `processing`: a refund PayPal reports PENDING, read until it ends (`reconcile_payment`);
    - otherwise the command's own status (`pending` and `unknown` are retried)."""
    if command["status"] == "rejected" and command["sent_at"] is None:
        return "refused"
    if command["status"] == "unknown" and (command["result"] or {}).get("status") == "PENDING":
        return "processing"
    return str(command["status"])


def _released(proposal_rows: list[DictRow], guest_id: str) -> int:
    total = 0
    for row in proposal_rows:
        index = row["choice"]["balance"]
        for line in row["parts"]["balance"][index]["lines"]:
            if line["guest_id"] == guest_id and line["kind"] == "refund":
                total += line["amount_minor"]
    return total


def _line(proposal: DictRow, purpose: str, guest_id: str) -> dict[str, Any] | None:
    """The guest's line in the chosen option; None for a guest invited after evaluation."""
    lines = proposal["parts"][purpose][proposal["choice"][purpose]]["lines"]
    return next((line for line in lines if line["guest_id"] == guest_id), None)


def _placed_late(line: dict[str, Any] | None) -> bool:
    """Paid or held after the settlement was evaluated, so no outcome covers it."""
    return line is None or line["base_minor"] == 0


def _moves(
    proposal: DictRow, guest: DictRow, accepted: list[DictRow]
) -> list[tuple[UUID, dict[str, Any]]]:
    """The commands this settlement sends now for one guest."""
    moves: list[tuple[UUID, dict[str, Any]]] = []
    guest_id = str(guest["id"])
    deposit = _line(proposal, "deposit", guest_id)
    if deposit is not None and deposit["kind"] == "refund" and deposit["amount_minor"] > 0:
        moves.append(
            (guest["deposit_id"], {"kind": "refund", "amount_minor": deposit["amount_minor"]})
        )
    if guest["balance_state"] == "authorized":
        if _placed_late(_line(proposal, "balance", guest_id)):
            # a hold placed while the trip was being cancelled as a whole: the trip will not
            # happen and no outcome claims it, so it is released
            owed = 0
        else:
            owed = guest["balance_minor"] - _released(accepted, guest_id)
        settlement = settle_payment(Payment("authorized", guest["balance_minor"]), max(owed, 0))
        if settlement.action == "void":
            moves.append((guest["balance_id"], {"kind": "void", "amount_minor": 0}))
    return moves


async def _late_deposit(conn: Conn, proposal: DictRow, guest: DictRow) -> None:
    """A deposit captured after evaluation: no outcome covers it, so the operator is asked."""
    if guest["deposit_state"] != "captured":
        return
    if not _placed_late(_line(proposal, "deposit", str(guest["id"]))):
        return
    trip = await trips.trip(conn, proposal["workspace_id"], proposal["trip_id"])
    await proposals.ask(
        conn,
        trip,
        proposals.NewAsk(
            "late_payment",
            f"payment:{guest['deposit_id']}",
            f"{guest['name']} paid the deposit ({usd(guest['deposit_minor'])}) while the"
            " trip was being cancelled, so no settlement covers it. Settle it with the guest"
            " outside tidehold, then dismiss this ask.",
            {"guest": guest["name"], "amount_minor": guest["deposit_minor"]},
        ),
    )


async def settle(conn: Conn, client: PayPalClient | None, proposal_id: UUID, now: datetime) -> None:
    proposal = await (
        await conn.execute("select * from proposals where id = %s", (proposal_id,))
    ).fetchone()
    if proposal is None or proposal["state"] not in ("accepted", "executed"):
        return  # decided otherwise
    # an executed settlement runs again only for payments placed late (`ensure_schedules`);
    # the commands it already sent are found and not sent again
    if client is None:
        raise due.RetryLater("PAYPAL_NOT_CONFIGURED")
    accepted = await (
        await conn.execute(
            "select parts, choice from proposals where trip_id = %s and kind = 'settlement'"
            " and state in ('accepted', 'executed')",
            (proposal["trip_id"],),
        )
    ).fetchall()
    statuses: list[str] = []
    unread = False
    for guest in await trips.guests(conn, proposal["trip_id"]):
        await _late_deposit(conn, proposal, guest)
        for payment_id, move in _moves(proposal, guest, accepted):
            status = await _send(conn, client, proposal, (payment_id, move), now)
            statuses.append(status)
            if status in ("rejected", "refused"):
                await _refused(conn, proposal, guest, (payment_id, move))
            if status in ("unknown", "processing"):
                unread = not await _being_read(conn, payment_id) or unread
    if not _final(statuses, unread=unread):
        return  # a refusal before PayPal keeps the settlement accepted, for the operator (8b)
    await conn.execute(
        "update proposals set state = 'executed' where id = %s and state = 'accepted'",
        (proposal_id,),
    )


def _final(statuses: list[str], *, unread: bool) -> bool:
    """Whether the settlement is executed now that its moves ran; raises to run it again while
    a move is not final."""
    if "unknown" in statuses or "pending" in statuses:
        raise due.RetryLater("PAYPAL_OUTCOME_STILL_UNKNOWN")
    if unread:
        raise due.RetryLater("PAYPAL_REFUND_NOT_READ")
    if "processing" in statuses:
        # run again on the read's own interval until every move is final: the read's wake
        # only speeds this up, and it is dropped while this action is running
        raise due.Waiting(service.PENDING_REFUND_READ_EVERY, "PAYPAL_REFUND_PENDING")
    return "refused" not in statuses


async def _refused(
    conn: Conn, proposal: DictRow, guest: DictRow, step: tuple[UUID, dict[str, Any]]
) -> None:
    """Ask the operator about a move that did not happen: refused by PayPal, or before PayPal
    was called (the code says which and why), once per command."""
    payment_id, move = step
    trip = await trips.trip(conn, proposal["workspace_id"], proposal["trip_id"])
    command = await _command(conn, payment_id, move["kind"], proposal["id"])
    if command is None:
        return
    by = "tidehold refused" if command["sent_at"] is None else "PayPal refused"
    await proposals.ask(
        conn,
        trip,
        proposals.NewAsk(
            "money_refused",
            f"command:{command['id']}",
            f"{by} the {move['kind']} for {guest['name']}"
            f" ({usd(move['amount_minor'])}, {command['error_code']}). Settle it with the guest"
            " outside tidehold, then dismiss this ask.",
            {
                "guest": guest["name"],
                "kind": move["kind"],
                "amount_minor": move["amount_minor"],
                "code": command["error_code"],
            },
        ),
    )


# Authorizations are valid 29 days: a payment placed late after that has expired or settled.
LATE_SWEEP = timedelta(days=30)


def _late_payments(
    proposal: DictRow, guests: list[DictRow], asked: set[str], voiding: set[UUID]
) -> bool:
    """Whether a payment the settlement did not cover is still unhandled: a late hold with no
    void sent for it, or a late deposit not yet asked about."""
    for guest in guests:
        guest_id = str(guest["id"])
        if (
            guest["balance_state"] == "authorized"
            and _placed_late(_line(proposal, "balance", guest_id))
            and guest["balance_id"] not in voiding
        ):
            return True
        if (
            guest["deposit_state"] == "captured"
            and _placed_late(_line(proposal, "deposit", guest_id))
            and f"payment:{guest['deposit_id']}" not in asked
        ):
            return True
    return False


async def ensure_schedules(conn: Conn, now: datetime) -> int:
    """Run a whole-trip settlement again when a payment it did not cover lands after it ran (an
    attempt in flight during the cancellation, or one reconciled later)."""
    rows = await (
        await conn.execute(
            "select p.* from proposals p join trips t on t.id = p.trip_id"
            " join cancellations c on c.id = p.cancellation_id"
            " where t.cancelled_at > %s and c.scope = 'rest' and c.withdrawn_at is null"
            " and p.state in ('accepted', 'executed')"
            " and not exists (select 1 from due_actions d where d.proposal_id = p.id"
            "   and d.kind = 'settle_proposal' and d.state in ('pending', 'running'))",
            (now - LATE_SWEEP,),
        )
    ).fetchall()
    scheduled = 0
    for proposal in rows:
        asked = {
            row["subject"]
            for row in await (
                await conn.execute(
                    "select subject from proposals where trip_id = %s and kind = 'late_payment'",
                    (proposal["trip_id"],),
                )
            ).fetchall()
        }
        voiding = {
            row["payment_id"]
            for row in await (
                await conn.execute(
                    "select payment_id from money_commands"
                    " where proposal_id = %s and kind = 'void'",
                    (proposal["id"],),
                )
            ).fetchall()
        }
        guests = await trips.guests(conn, proposal["trip_id"])
        if _late_payments(proposal, guests, asked, voiding):
            await due.schedule(conn, "settle_proposal", proposal["id"], due_at=now)
            scheduled += 1
    return scheduled


async def settle_due(
    conn: Conn, action: due.Action, now: datetime, client: PayPalClient | None
) -> None:
    if action.proposal_id is None:
        raise ValueError("settle_proposal needs a proposal")
    await settle(conn, client, action.proposal_id, now)
