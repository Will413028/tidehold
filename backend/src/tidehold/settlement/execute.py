"""The `settle_proposal` due action: move the money an accepted settlement decided, day or
whole trip (plan step 8b, D18, D21). What each line does comes from `settlement.moves`: a
refund of a captured payment is sent to PayPal, a refund of a payment not yet paid lowers
what is due, and a credit is written once its payment is captured. Each command records the
proposal that decided it.

The handler re-reads the proposal before acting (a cancellation or decision since it was
scheduled supersedes it by state, D17), and a rerun finds the commands, waivers and credits an
earlier run stored, so it resumes instead of moving money twice. A settlement stays `accepted`
while a line waits for its payment (an attempt at PayPal, or a credit not yet paid for); the
schedules sweep runs it again once that payment can move.
"""

from datetime import datetime
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
from tidehold.settlement import moves, proposals
from tidehold.trips import store as trips


async def _command(conn: Conn, payment_id: UUID, kind: str, proposal_id: UUID) -> DictRow | None:
    row: DictRow | None = await (
        await conn.execute(
            "select id, status, sent_at, error_code, result, amount_minor, read_at"
            " from money_commands"
            " where payment_id = %s and kind = %s"
            " and proposal_id = %s order by created_at desc limit 1",
            (payment_id, kind, proposal_id),
        )
    ).fetchone()
    return row


async def _record(
    conn: Conn, proposal: DictRow, payment_id: UUID, move: dict[str, Any], now: datetime
) -> DictRow | None:
    """Store the refund, or None when a concurrent run of this settlement stored it first (the
    unique index keeps one live refund per proposal and payment). A refund refused before
    PayPal is stored closed, so this guest's move ends there and the others go on."""
    try:
        async with conn.transaction():
            return await commands.record_refund(
                conn,
                payment_id,
                amount_minor=move["amount_minor"],
                now=now,
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


async def _being_read(conn: Conn, payment_id: UUID, command: DictRow) -> bool:
    """Whether a `reconcile_payment` reads this open command, scheduling one when the last
    ended without giving up on it (its wake can be dropped while it runs); False once a failed
    one had read it (the reads gave up), which the operator console shows."""
    last = await (
        await conn.execute(
            "select state, created_at from due_actions where kind = 'reconcile_payment'"
            " and payment_id = %s order by id desc limit 1",
            (payment_id,),
        )
    ).fetchone()
    if last is not None and last["state"] in ("pending", "running"):
        return True
    if (
        last is not None
        and last["state"] == "failed"
        and command["read_at"] is not None
        and command["read_at"] >= last["created_at"]
    ):
        return False
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


async def _waive(conn: Conn, proposal: DictRow, line: moves.Line) -> str:
    """A refund of a payment not yet paid: `final` once waived, `refund` when it was captured
    meanwhile, `wait` while an attempt to pay it is at PayPal, `refused` above what is due."""
    try:
        async with conn.transaction():
            waived = await moves.waive(conn, proposal, line)
    except Conflict as error:
        if error.code != "WAIVER_EXCEEDS_DUE":
            raise
        await _waiver_refused(conn, proposal, line)
        return "refused"
    return {"waived": "final", "already": "final", "captured": "refund", "in_flight": "wait"}[
        waived
    ]


async def _settle_line(
    conn: Conn, client: PayPalClient | None, proposal: DictRow, line: moves.Line, now: datetime
) -> tuple[str, bool]:
    """Make one line's move; its status for `_final`, and whether its refund is no longer read
    (`_being_read`)."""
    move = moves.move(line)
    if line.kind == "refund" and await moves.waived(conn, proposal, line):
        return "final", False  # taken off before it was paid; a capture since owes no refund
    if move == "waive":
        outcome = await _waive(conn, proposal, line)
        if outcome != "refund":
            return outcome, False
        move = "refund"  # captured meanwhile
    if move == "credit":
        async with conn.transaction():
            await moves.credit(conn, proposal, line)
        return "final", False
    if move != "refund":
        return move, False  # `none`, or `wait` for its payment
    if client is None:
        raise due.RetryLater("PAYPAL_NOT_CONFIGURED")
    step = (line.payment_id, {"kind": "refund", "amount_minor": line.amount_minor})
    status = await _send(conn, client, proposal, step, now)
    if status in ("rejected", "refused"):
        await _refused(conn, proposal, line.guest, step)
    unread = False
    if status in ("unknown", "processing"):
        command = await _command(conn, line.payment_id, "refund", proposal["id"])
        unread = command is None or not await _being_read(conn, line.payment_id, command)
    return status, unread


async def settle(conn: Conn, client: PayPalClient | None, proposal_id: UUID, now: datetime) -> None:
    proposal = await (
        await conn.execute("select * from proposals where id = %s", (proposal_id,))
    ).fetchone()
    if proposal is None or proposal["state"] != "accepted":
        return  # decided otherwise, or done
    statuses: list[str] = []
    unread = False
    for line in moves.lines(proposal, await trips.guests(conn, proposal["trip_id"])):
        status, gone = await _settle_line(conn, client, proposal, line, now)
        statuses.append(status)
        unread = unread or gone
    if not _final(statuses, unread=unread) or "wait" in statuses:
        # a refusal keeps the settlement accepted, for the operator; a line waiting for its
        # payment runs again from the sweep once that payment can move
        return
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


def _refused_text(by: str, kind: str, name: str, amount: int, code: str | None) -> str:
    if code == "REFUND_WINDOW_CLOSED":
        return (
            f"{by} the {kind} for {name} ({usd(amount)}): the payment was captured more than"
            " 180 days ago, past PayPal's refund window. Refund the guest another way, or offer"
            " a credit, then dismiss this ask."
        )
    return (
        f"{by} the {kind} for {name} ({usd(amount)}, {code}). Settle it with the guest"
        " outside tidehold, then dismiss this ask."
    )


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
            _refused_text(
                by, move["kind"], guest["name"], move["amount_minor"], command["error_code"]
            ),
            {
                "guest": guest["name"],
                "kind": move["kind"],
                "amount_minor": move["amount_minor"],
                "code": command["error_code"],
            },
        ),
    )


async def _waiver_refused(conn: Conn, proposal: DictRow, line: moves.Line) -> None:
    """A waiver above what is still due: netting through earlier settlements makes it
    unreachable, so it stops for the operator instead of lowering the amount silently."""
    trip = await trips.trip(conn, proposal["workspace_id"], proposal["trip_id"])
    await proposals.ask(
        conn,
        trip,
        proposals.NewAsk(
            "money_refused",
            f"waiver:{proposal['id']}:{line.payment_id}",
            _refused_text(
                "tidehold refused",
                f"reduction of the {line.purpose}",
                line.guest["name"],
                line.amount_minor,
                "WAIVER_EXCEEDS_DUE",
            ),
            {
                "guest": line.guest["name"],
                "kind": "waiver",
                "amount_minor": line.amount_minor,
                "code": "WAIVER_EXCEEDS_DUE",
            },
        ),
    )


# For each move that can act on its own, what shows it already acted (or is at PayPal).
_DONE: dict[moves.Move, str] = {
    "credit": "select 1 from credits where proposal_id = %(proposal)s and guest_id = %(guest)s"
    " and purpose = %(purpose)s",
    # a refund line is done once refunded or waived (a waived payment captured since owes none)
    "refund": "select 1 from money_commands where proposal_id = %(proposal)s"
    " and payment_id = %(payment)s and kind = 'refund'"
    " union all select 1 from payment_waivers where proposal_id = %(proposal)s"
    " and payment_id = %(payment)s",
    # a waiver is done once stored, refused (its ask is open), or waiting on an attempt at PayPal
    "waive": "select 1 from payment_waivers where proposal_id = %(proposal)s"
    " and payment_id = %(payment)s"
    " union all select 1 from proposals where kind = 'money_refused'"
    " and subject = 'waiver:' || %(proposal)s || ':' || %(payment)s"
    " union all select 1 from money_commands where payment_id = %(payment)s and kind = 'open'"
    " and status in ('pending', 'unknown') and sent_at is not null",
}


async def _can_move(conn: Conn, proposal: DictRow, line: moves.Line) -> bool:
    """Whether a line of an accepted settlement can move now and has not: its payment was
    captured and its refund or credit is not stored, or it is unpaid, not at PayPal, and not
    waived. A line still waiting, or a refund already handed to PayPal, does not count: the
    refund's own retries and reads finish it."""
    move = moves.move(line)
    if move not in _DONE:
        return False
    params = {
        "proposal": proposal["id"],
        "guest": line.guest["id"],
        "purpose": line.purpose,
        "payment": line.payment_id,
    }
    return await (await conn.execute(_DONE[move], params)).fetchone() is None


async def ensure_schedules(conn: Conn, now: datetime) -> int:
    """Run an accepted settlement again when one of its lines can move now: the payment it
    waited for was captured (refund or credit it), or its attempt at PayPal ended unpaid
    (waive it). A settlement whose lines all wait costs only this read."""
    rows = await (
        await conn.execute(
            "select p.* from proposals p where p.kind = 'settlement' and p.state = 'accepted'"
            " and not exists (select 1 from due_actions d where d.proposal_id = p.id"
            "   and d.kind = 'settle_proposal' and d.state in ('pending', 'running'))"
        )
    ).fetchall()
    scheduled = 0
    for proposal in rows:
        for line in moves.lines(proposal, await trips.guests(conn, proposal["trip_id"])):
            if await _can_move(conn, proposal, line):
                await due.schedule(conn, "settle_proposal", proposal["id"], due_at=now)
                scheduled += 1
                break
    return scheduled


async def settle_due(
    conn: Conn, action: due.Action, now: datetime, client: PayPalClient | None
) -> None:
    if action.proposal_id is None:
        raise ValueError("settle_proposal needs a proposal")
    await settle(conn, client, action.proposal_id, now)
