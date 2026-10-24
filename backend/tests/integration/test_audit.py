"""The nightly reconciliation (plan step 10a): every money command is listed with PayPal's
state, and what PayPal does not confirm is reported. Nothing is sent to PayPal."""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from psycopg.rows import DictRow

from tests.integration.fake_paypal import FakePayPal
from tidehold.bootstrap import nightly_audit
from tidehold.identity.sessions import new_workspace
from tidehold.money import audit, commands, service
from tidehold.money.commands import Purpose
from tidehold.money.paypal import TEST_CARD
from tidehold.platform.db import Conn

CAP = 30


async def _paid(conn: Conn, fake: FakePayPal, purpose: Purpose) -> UUID:
    workspace_id, _ = await new_workspace(conn)
    payment_id = await commands.create_payment(
        conn, workspace_id=workspace_id, purpose=purpose, amount_minor=30000
    )
    await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)
    return payment_id


async def _refund(conn: Conn, fake: FakePayPal, payment_id: UUID, amount: int) -> DictRow:
    async with conn.transaction():
        command = await commands.record_refund(
            conn, payment_id, amount_minor=amount, now=datetime.now(UTC), proposal_id=None
        )
    await service.execute(conn, fake.client(), command["id"])
    return command


def _verdicts(rows: list[audit.Row]) -> dict[UUID, str]:
    return {row.command_id: row.verdict for row in rows if row.command_id is not None}


async def test_every_command_is_listed_with_paypals_state(conn: Conn) -> None:
    fake = FakePayPal()
    deposit = await _paid(conn, fake, "deposit")
    balance = await _paid(conn, fake, "balance")
    completed = await _refund(conn, fake, balance, 5000)
    fake.refuse.add("refund")
    refused = await _refund(conn, fake, balance, 1000)
    fake.refuse.clear()
    fake.refund_status = "PENDING"
    pending = await _refund(conn, fake, deposit, 2000)
    fake.calls.clear()

    rows = await audit.run(conn, fake.client())

    every = await (await conn.execute("select id, kind, status from money_commands")).fetchall()
    assert len(every) == 5
    verdicts = _verdicts(rows)
    assert set(verdicts) == {row["id"] for row in every}
    assert verdicts[completed["id"]] == "match"
    assert verdicts[refused["id"]] == "match"
    assert verdicts[pending["id"]] == "open"
    opens = [row["id"] for row in every if row["kind"] == "open"]
    assert [verdicts[i] for i in opens] == ["match", "match"]
    by_id = {row.command_id: row for row in rows}
    assert by_id[pending["id"]].paypal == {"refund": "PENDING", "amount_minor": 2000}
    # the pending refund is unsettled and the rebuild drops it: the run fails
    assert audit.failed(rows)
    assert [c for c in fake.calls if not c.startswith("GET ")] == []


async def test_a_refund_marked_succeeded_that_paypal_does_not_list_is_reported(
    conn: Conn,
) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    command = await _refund(conn, fake, payment_id, 5000)
    # the seeded mismatch: PayPal's order no longer lists the refund we recorded as done
    order = fake.orders[(await commands.payment(conn, payment_id))["order_id"]]
    order["purchase_units"][0]["payments"]["refunds"] = []

    rows = await audit.run(conn, fake.client())

    assert _verdicts(rows)[command["id"]] == "mismatch"
    assert audit.failed(rows)
    payment_rows = [r for r in rows if r.command_id is None and r.payment_id == payment_id]
    assert Counter(r.verdict for r in payment_rows) == Counter({"mismatch": 1, "match": 1})


async def test_a_refund_at_paypal_with_no_command_is_reported(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    order = fake.orders[(await commands.payment(conn, payment_id))["order_id"]]
    order["purchase_units"][0]["payments"]["refunds"] = [
        {
            "id": "RSOMEONEELSE001",
            "status": "COMPLETED",
            "amount": {"currency_code": "USD", "value": "10.00"},
            "invoice_id": "not-ours",
        }
    ]

    rows = await audit.run(conn, fake.client())

    reasons = {r.reason for r in rows if r.verdict == "mismatch"}
    assert reasons == {"REFUND_WITHOUT_COMMAND", "REFUNDED_TOTAL_DIFFERS"}


async def test_a_failed_read_is_reported_as_unread(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    fake.fail_reads.add("/v2/checkout/orders")

    rows = await audit.run(conn, fake.client())

    assert {r.verdict for r in rows if r.payment_id == payment_id} == {"unread"}
    assert audit.failed(rows)


async def test_a_clean_day_passes(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    await _refund(conn, fake, payment_id, 30000)

    rows = await audit.run(conn, fake.client())

    assert {r.verdict for r in rows} == {"match"}
    assert not audit.failed(rows)


async def test_the_evidence_log_holds_every_row_and_a_summary(conn: Conn, tmp_path: Path) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    command = await _refund(conn, fake, payment_id, 5000)
    rows = await audit.run(conn, fake.client())

    summary = nightly_audit.write_log(tmp_path, rows, datetime(2026, 11, 1, 11, tzinfo=UTC))

    lines = [
        json.loads(line)
        for line in (tmp_path / "reconcile-2026-11-01.jsonl").read_text().splitlines()
    ]
    assert len(lines) == len(rows) + 1
    assert str(command["id"]) in {line.get("command_id") for line in lines}
    assert lines[-1] == summary
    assert summary["failed"] is False
