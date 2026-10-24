"""The nightly reconciliation (plan step 10a; design:
docs/plans/2026-10-19-tidehold-hackathon/step10a-design.md).

Every money command and every placed payment is compared with what PayPal's order shows.
It runs right before the nightly rebuild empties the database, so the database holds exactly
what was recorded since the last rebuild, and every row is read. It only reads: the rows are
selected first, then each order is read once, outside any transaction; the caller passes a
client built on `paypal.ReadOnlyTransport`, so nothing can be sent to PayPal.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.money.paypal import (
    Capture,
    PayPalError,
    PayPalUnknownOutcome,
    Refund,
    captures_of,
    refunds_of,
)
from tidehold.platform.db import Conn

# `open`: PayPal's outcome was never settled; the rebuild drops the command unsettled.
# `not_readable`: a command PayPal refused before an order existed, so nothing can be read.
Verdict = Literal["match", "mismatch", "open", "unread", "not_readable"]
FAILING: frozenset[Verdict] = frozenset({"mismatch", "open", "unread"})


class OrderReads(Protocol):
    async def get_order(self, order_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Row:
    """One line of the evidence log: a command (`command_id` set) or a payment check."""

    payment_id: UUID
    workspace_id: UUID
    check: str  # the command's kind, or captured_total, refunded_total, refund_at_paypal
    verdict: Verdict
    command_id: UUID | None = None
    reason: str | None = None
    local: dict[str, Any] = field(default_factory=dict)
    paypal: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("payment_id", "workspace_id", "command_id"):
            row[key] = None if row[key] is None else str(row[key])
        return row


def failed(rows: list[Row]) -> bool:
    return any(row.verdict in FAILING for row in rows)


@dataclass(frozen=True)
class _Order:
    order_id: str
    status: str | None
    captures: list[Capture]
    refunds: list[Refund]

    @property
    def completed_captures(self) -> list[Capture]:
        return [c for c in self.captures if c.status == "COMPLETED"]


class _Orders:
    """Each order read once; a read that failed, or an answer we cannot read, is unread."""

    def __init__(self, reads: OrderReads) -> None:
        self._reads = reads
        self._seen: dict[str, _Order | None] = {}

    async def get(self, order_id: str) -> _Order | None:
        if order_id not in self._seen:
            try:
                order = await self._reads.get_order(order_id)
                status = order.get("status")
                self._seen[order_id] = _Order(
                    order_id,
                    None if status is None else str(status),
                    captures_of(order),
                    refunds_of(order),
                )
            except (PayPalError, PayPalUnknownOutcome):
                self._seen[order_id] = None
        return self._seen[order_id]


def _order_of_open(command: DictRow) -> str | None:
    found = command["order_id"] or (command["result"] or {}).get("orderId")
    return None if found is None else str(found)


def _row(payment: DictRow, check: str, verdict: Verdict, **rest: Any) -> Row:
    return Row(payment["id"], payment["workspace_id"], check, verdict, **rest)


def _local(command: DictRow) -> dict[str, Any]:
    return {"status": command["status"], "amount_minor": command["amount_minor"]}


async def _open(orders: _Orders, command: DictRow, payment: DictRow) -> Row:
    local = _local(command)
    order_id = _order_of_open(command)
    if order_id is None:
        reason = None
        if command["status"] in ("pending", "unknown"):
            verdict: Verdict = "open"
        elif command["status"] == "rejected" and command["sent_at"] is None:
            verdict = "match"  # replaced before it reached PayPal
        elif command["status"] == "rejected":
            verdict = "not_readable"  # PayPal refused it before creating an order
        else:
            verdict, reason = "mismatch", "NO_ORDER_ID"
        return _row(payment, "open", verdict, command_id=command["id"], reason=reason, local=local)
    order = await orders.get(order_id)
    if order is None:
        return _row(payment, "open", "unread", command_id=command["id"], local=local)
    completed = order.completed_captures
    shown = {
        "order": order_id,
        "status": order.status,
        "captures": [{"id": c.capture_id, "amount_minor": c.amount_minor} for c in completed],
    }
    reason = None
    match command["status"]:
        case "succeeded":
            ours = [c for c in completed if c.capture_id == payment["capture_id"]]
            if not ours:
                reason = "CAPTURE_MISSING"
            elif ours[0].amount_minor != command["amount_minor"]:
                reason = "CAPTURE_AMOUNT_DIFFERS"
        case "rejected":
            reason = "CAPTURED_BUT_REJECTED" if completed else None
        case _:
            return _row(
                payment, "open", "open", command_id=command["id"], local=local, paypal=shown
            )
    return _row(
        payment,
        "open",
        "mismatch" if reason else "match",
        command_id=command["id"],
        reason=reason,
        local=local,
        paypal=shown,
    )


def _refund_of(command: DictRow, refunds: list[Refund]) -> Refund | None:
    refund_id = (command["result"] or {}).get("refundId")
    for refund in refunds:
        if refund.refund_id == refund_id or refund.invoice_id == command["invoice_id"]:
            return refund
    return None


def _refund(command: DictRow, payment: DictRow, refunds: list[Refund] | None) -> Row:
    local = _local(command)
    if refunds is None:
        return _row(payment, "refund", "unread", command_id=command["id"], local=local)
    found = _refund_of(command, refunds)
    shown: dict[str, Any] = (
        {"refund": None}
        if found is None
        else {"refund": found.status, "amount_minor": found.amount_minor}
    )
    reason = None
    match command["status"]:
        case "succeeded":
            if found is None or found.status != "COMPLETED":
                reason = "REFUND_MISSING"
            elif found.amount_minor != command["amount_minor"]:
                reason = "REFUND_AMOUNT_DIFFERS"
        case "rejected":
            completed = found is not None and found.status == "COMPLETED"
            reason = "REFUNDED_BUT_REJECTED" if completed else None
        case _:
            return _row(
                payment, "refund", "open", command_id=command["id"], local=local, paypal=shown
            )
    return _row(
        payment,
        "refund",
        "mismatch" if reason else "match",
        command_id=command["id"],
        reason=reason,
        local=local,
        paypal=shown,
    )


def _totals(payment: DictRow, order: _Order | None, commands: list[DictRow]) -> list[Row]:
    """What the payment row says was captured and refunded, against PayPal's order, and every
    refund PayPal lists that no command of ours explains."""
    checks = ("captured_total", "refunded_total")
    if order is None:
        return [_row(payment, check, "unread") for check in checks]
    captured = sum(c.amount_minor for c in order.completed_captures)
    refunded = sum(r.amount_minor for r in order.refunds if r.status == "COMPLETED")
    rows = [
        _row(
            payment,
            check,
            "match" if ours == theirs else "mismatch",
            reason=None if ours == theirs else reason,
            local={"amount_minor": ours},
            paypal={"amount_minor": theirs},
        )
        for check, ours, theirs, reason in (
            ("captured_total", payment["captured_minor"], captured, "CAPTURED_TOTAL_DIFFERS"),
            ("refunded_total", payment["refunded_minor"], refunded, "REFUNDED_TOTAL_DIFFERS"),
        )
    ]
    ours = [c for c in commands if c["kind"] == "refund"]
    for refund in order.refunds:
        if not any(_refund_of(command, [refund]) for command in ours):
            rows.append(
                _row(
                    payment,
                    "refund_at_paypal",
                    "mismatch",
                    reason="REFUND_WITHOUT_COMMAND",
                    paypal={"refund": refund.refund_id, "status": refund.status},
                )
            )
    return rows


async def run(conn: Conn, reads: OrderReads) -> list[Row]:
    payments = await (await conn.execute("select * from payments order by created_at")).fetchall()
    every = await (
        await conn.execute("select * from money_commands order by created_at")
    ).fetchall()
    orders = _Orders(reads)
    rows: list[Row] = []
    for payment in payments:
        commands = [c for c in every if c["payment_id"] == payment["id"]]
        order = None if payment["order_id"] is None else await orders.get(payment["order_id"])
        unread = payment["order_id"] is not None and order is None
        refunds = None if unread else ([] if order is None else order.refunds)
        for command in commands:
            if command["kind"] == "open":
                rows.append(await _open(orders, command, payment))
            else:
                rows.append(_refund(command, payment, refunds))
        if payment["order_id"] is not None:
            rows.extend(_totals(payment, order, commands))
    return rows
