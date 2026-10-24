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

# `open`: sent, but PayPal's outcome was never settled; the rebuild drops it unsettled.
# `not_sent`: recorded but never sent (an abandoned checkout), and PayPal shows no money moved.
# `not_readable`: a command PayPal refused before an order existed, so nothing can be read.
Verdict = Literal["match", "mismatch", "open", "unread", "not_sent", "not_readable"]
# PayPal's capture statuses (Payments v2): money that reached the merchant stays captured when
# refunded (PARTIALLY_REFUNDED, REFUNDED); PENDING is money still in flight.
CAPTURED = frozenset({"COMPLETED", "PARTIALLY_REFUNDED", "REFUNDED"})
MOVING = CAPTURED | {"PENDING"}
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
    def captured(self) -> list[Capture]:
        return [c for c in self.captures if c.status in CAPTURED]

    @property
    def moving(self) -> list[Capture]:
        """Captured, or still in flight: money a rejected or unsent attempt must not show."""
        return [c for c in self.captures if c.status in MOVING]


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


def _without_order(command: DictRow, *, unsent: bool) -> tuple[Verdict, str | None]:
    """An attempt with no order id: PayPal can show nothing for it."""
    if unsent:
        return "not_sent", None  # abandoned before its order existed
    if command["status"] == "rejected" and command["sent_at"] is None:
        return "match", None  # replaced, or refused before its order existed
    if command["status"] == "rejected":
        return "not_readable", None  # sent, and PayPal refused it before creating an order
    if command["status"] == "unknown":
        return "open", None
    return "mismatch", "NO_ORDER_ID"


def _on_order(
    command: DictRow, payment: DictRow, order: _Order, *, unsent: bool
) -> tuple[Verdict, str | None]:
    match command["status"]:
        case "succeeded":
            ours = [c for c in order.captured if c.capture_id == payment["capture_id"]]
            if not ours:
                return "mismatch", "CAPTURE_MISSING"
            if ours[0].amount_minor != command["amount_minor"]:
                return "mismatch", "CAPTURE_AMOUNT_DIFFERS"
            return "match", None
        case "rejected":
            return ("mismatch", "CAPTURED_BUT_REJECTED") if order.moving else ("match", None)
        case _ if unsent:
            return ("mismatch", "CAPTURED_BUT_UNSENT") if order.moving else ("not_sent", None)
        case _:
            return "open", None


async def _open(orders: _Orders, command: DictRow, payment: DictRow) -> Row:
    local = _local(command)
    unsent = command["status"] == "pending" and command["sent_at"] is None
    order_id = _order_of_open(command)
    if order_id is None:
        verdict, reason = _without_order(command, unsent=unsent)
        return _row(payment, "open", verdict, command_id=command["id"], reason=reason, local=local)
    order = await orders.get(order_id)
    if order is None:
        return _row(payment, "open", "unread", command_id=command["id"], local=local)
    shown = {
        "order": order_id,
        "status": order.status,
        "captures": [
            {"id": c.capture_id, "status": c.status, "amount_minor": c.amount_minor}
            for c in order.captures
        ],
    }
    verdict, reason = _on_order(command, payment, order, unsent=unsent)
    return _row(
        payment, "open", verdict, command_id=command["id"], reason=reason, local=local, paypal=shown
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
    # COMPLETED moved money; PENDING is still moving it
    moving = found is not None and found.status in ("COMPLETED", "PENDING")
    verdict: Verdict = "match"
    reason: str | None = None
    match command["status"]:
        case "succeeded" if found is None or found.status != "COMPLETED":
            verdict, reason = "mismatch", "REFUND_MISSING"
        case "succeeded" if found is not None and found.amount_minor != command["amount_minor"]:
            verdict, reason = "mismatch", "REFUND_AMOUNT_DIFFERS"
        case "rejected" if moving:
            verdict, reason = "mismatch", "REFUNDED_BUT_REJECTED"
        case "pending" if command["sent_at"] is None:
            verdict, reason = ("mismatch", "REFUNDED_BUT_UNSENT") if moving else ("not_sent", None)
        case "pending" | "unknown":
            verdict = "open"
        case _:
            pass
    return _row(
        payment,
        "refund",
        verdict,
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
    captured = sum(c.amount_minor for c in order.captured)
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
