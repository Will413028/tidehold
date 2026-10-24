"""What one line of an accepted settlement does to its payment (plan step 8b, D18, D21).

A line's amount is computed at decision on what the guest owes on the payment, whether it is
paid yet or not (`evaluate`); what it does is decided when it executes, from the payment's
state: a refund of a captured payment goes to PayPal, a refund of a payment not yet paid lowers
what is due on it (a waiver), and a credit is written once the payment is captured. Design:
docs/plans/2026-10-19-tidehold-hackathon/step8b-design.md.

Both the decision (`proposals._accept`, inside its transaction) and the `settle_proposal`
handler run these moves; the decision runs only those that need no PayPal call.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.money import commands
from tidehold.platform.db import Conn
from tidehold.platform.errors import Conflict
from tidehold.settlement.evaluate import PURPOSES, Purpose

Move = Literal["refund", "waive", "credit", "wait", "none"]
State = Literal["pending", "captured"]


@dataclass(frozen=True)
class Line:
    """One guest's chosen outcome on one payment."""

    guest: DictRow
    purpose: Purpose
    kind: str
    amount_minor: int
    credit_expiry_months: int | None

    @property
    def payment_id(self) -> UUID:
        payment_id: UUID = self.guest[f"{self.purpose}_id"]
        return payment_id

    @property
    def state(self) -> State:
        state: State = self.guest[f"{self.purpose}_state"]
        return state


def lines(proposal: Mapping[str, Any], guests: list[DictRow]) -> Iterator[Line]:
    """The chosen lines of an accepted settlement for the guests it lists. A released guest's
    balance lines are dropped (it owes nothing more on the balance, whose rest was waived when
    it was released); its deposit lines stand as decided."""
    by_id = {str(g["id"]): g for g in guests}
    for purpose in PURPOSES:
        option = proposal["parts"][purpose][proposal["choice"][purpose]]
        for line in option["lines"]:
            guest = by_id.get(line["guest_id"])
            if guest is None or (purpose == "balance" and guest["released_at"] is not None):
                continue
            yield Line(
                guest,
                purpose,
                line["kind"],
                line["amount_minor"],
                line["credit_expiry_months"],
            )


def move(line: Line) -> Move:
    """The move a line makes on its payment as it stands now."""
    if line.kind == "none" or line.amount_minor == 0:
        return "none"
    match line.state:
        case "captured":
            return "refund" if line.kind == "refund" else "credit"
        case "pending":
            return "waive" if line.kind == "refund" else "wait"


async def waived(conn: Conn, proposal: Mapping[str, Any], line: Line) -> bool:
    """Whether this settlement already took the line off its payment before it was paid: the
    line is then done, even once the payment is captured (it must not be refunded too)."""
    row = await (
        await conn.execute(
            "select 1 from payment_waivers where proposal_id = %s and payment_id = %s",
            (proposal["id"], line.payment_id),
        )
    ).fetchone()
    return row is not None


async def waive(conn: Conn, proposal: Mapping[str, Any], line: Line) -> commands.Waived:
    return await commands.waive(
        conn, line.payment_id, amount_minor=line.amount_minor, proposal_id=proposal["id"]
    )


def add_months(at: datetime, months: int) -> datetime:
    """The same day `months` later, clamped to the month's last day."""
    month = at.month - 1 + months
    year, month = at.year + month // 12, month % 12 + 1
    for day in range(at.day, 0, -1):
        try:
            return at.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise ValueError("no day in month")  # unreachable: every month has a first day


async def credit(conn: Conn, proposal: Mapping[str, Any], line: Line) -> None:
    """Write the line's credit once (one per proposal, guest and purpose); it expires counted
    from the decision, also when written later, once the payment is captured."""
    months = line.credit_expiry_months
    await conn.execute(
        "insert into credits (workspace_id, guest_id, proposal_id, purpose, amount_minor,"
        " expires_at) values (%s, %s, %s, %s, %s, %s)"
        " on conflict (proposal_id, guest_id, purpose) do nothing",
        (
            proposal["workspace_id"],
            line.guest["id"],
            proposal["id"],
            line.purpose,
            line.amount_minor,
            add_months(proposal["decided_at"], months) if months else None,
        ),
    )


async def run_without_paypal(conn: Conn, proposal: Mapping[str, Any], line: Line) -> bool:
    """Make the line's move if it needs no PayPal call; whether the line is final. A waiver the
    payment refuses (captured meanwhile, or an attempt at PayPal) is left to `settle_proposal`,
    as is a refusal above what is due, which asks there."""
    match move(line):
        case "none":
            return True
        case "credit":
            await credit(conn, proposal, line)
            return True
        case "waive":
            try:
                async with conn.transaction():
                    return await waive(conn, proposal, line) in ("waived", "already")
            except Conflict as error:
                if error.code != "WAIVER_EXCEEDS_DUE":
                    raise
                return False
        case "refund" | "wait":
            return False
