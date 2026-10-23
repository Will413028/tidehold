"""The money engine against a real database and an in-memory PayPal.

Ported from dive-trip-agent@8065eba `tests/test_deposits_{transactions,service,join}.py`,
keyed by payment instead of booking and diver.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import DictRow

from tests.integration.fake_paypal import FakePayPal
from tidehold.identity.sessions import new_workspace
from tidehold.money import commands, service
from tidehold.money.commands import Intent, Purpose
from tidehold.money.paypal import TEST_CARD, PayPalUnknownOutcome
from tidehold.platform.db import Conn, connect
from tidehold.platform.errors import Conflict, LimitReached

CAP = 30


async def _workspace(conn: Conn) -> UUID:
    workspace_id, _ = await new_workspace(conn)
    return workspace_id


async def _payment(
    conn: Conn,
    purpose: Purpose = "balance",
    amount: int = 30000,
    workspace: UUID | None = None,
    intent: Intent | None = None,
) -> UUID:
    return await commands.create_payment(
        conn,
        workspace_id=workspace or await _workspace(conn),
        purpose=purpose,
        intent=intent or ("capture" if purpose == "deposit" else "authorize"),
        amount_minor=amount,
    )


async def _paid(conn: Conn, fake: FakePayPal, purpose: Purpose = "balance") -> UUID:
    payment_id = await _payment(conn, purpose)
    await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)
    return payment_id


async def _payment_row(conn: Conn, payment_id: UUID) -> dict[str, object]:
    return dict(await commands.payment(conn, payment_id))


async def _refund(
    conn: Conn, payment_id: UUID, amount: int, now: datetime | None = None
) -> DictRow:
    async with conn.transaction():
        return await commands.record_refund(
            conn, payment_id, amount_minor=amount, now=now or datetime.now(UTC), proposal_id=None
        )


async def _command(conn: Conn, command_id: UUID) -> dict[str, object]:
    row = await (
        await conn.execute("select * from money_commands where id = %s", (command_id,))
    ).fetchone()
    assert row is not None
    return dict(row)


async def test_a_balance_is_held_then_partially_captured_once(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake)
    async with conn.transaction():
        command = await commands.record_command(conn, payment_id, "capture", amount_minor=20000)

    outcome = await service.execute(conn, fake.client(), command["id"])

    row = await _payment_row(conn, payment_id)
    assert outcome == "succeeded"
    assert (row["state"], row["captured_minor"]) == ("captured", 20000)
    assert command["target_id"] == (await _command(conn, command["id"]))["target_id"]


async def test_a_deposit_is_captured_then_refunded(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    command = await _refund(conn, payment_id, 5000)

    await service.execute(conn, fake.client(), command["id"])

    row = await _payment_row(conn, payment_id)
    assert (row["state"], row["captured_minor"], row["refunded_minor"]) == ("captured", 30000, 5000)
    assert command["target_id"] == row["capture_id"]


async def test_a_second_live_command_of_the_same_kind_is_refused(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake)
    async with conn.transaction():
        await commands.record_command(conn, payment_id, "void", amount_minor=0)

    with pytest.raises(psycopg.errors.UniqueViolation):
        async with conn.transaction():
            await commands.record_command(conn, payment_id, "void", amount_minor=0)
    # A capture closes the same hold, so it is refused too: money moves once (D11).
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with conn.transaction():
            await commands.record_command(conn, payment_id, "capture", amount_minor=100)
    # Refunds may be several (D18), but never above what the payment captured.
    deposit = await _paid(conn, fake, "deposit")
    with pytest.raises(Conflict, match="REFUND_EXCEEDS_CAPTURED"):
        await _refund(conn, deposit, 30001)


async def test_an_unknown_capture_is_reconciled_with_the_same_request_id(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake)
    fake.time_out_first.add("capture")
    async with conn.transaction():
        command = await commands.record_command(conn, payment_id, "capture", amount_minor=30000)

    first = await service.execute(conn, fake.client(), command["id"])
    second = await service.reconcile(conn, fake.client(), payment_id)

    assert first == "unknown"
    assert second == {command["id"]: "succeeded"}
    assert fake.moved("-" + str(command["id"])) == 1
    assert (await _payment_row(conn, payment_id))["state"] == "captured"


async def test_an_unexpected_paypal_answer_keeps_the_attempt_open(conn: Conn) -> None:
    fake = FakePayPal()
    fake.card_status = "PENDING"
    payment_id = await _payment(conn)

    with pytest.raises(PayPalUnknownOutcome):
        await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)

    [command_id] = await commands.open_commands(conn, payment_id)
    command = await _command(conn, command_id)
    assert (command["status"], command["error_code"]) == (
        "unknown",
        "PAYPAL_AUTHORIZATION_UNEXPECTED",
    )
    with pytest.raises(Conflict, match="PAYMENT_ATTEMPT_IN_FLIGHT"):
        await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)


async def _lock_waiters(conn: Conn) -> int:
    row = await (
        await conn.execute(
            "select count(*) as n from pg_stat_activity where wait_event_type = 'Lock'"
            " and query like '%%set sent_at%%'"
        )
    ).fetchone()
    return int(row["n"]) if row else 0


async def test_a_replacement_and_a_send_never_both_win(conn: Conn, database_url: str) -> None:
    """Whichever commits first wins: a replacement that holds the attempt's row makes the
    waiting send find it closed; a send that went first makes the replacement refuse."""
    fake = FakePayPal()
    payment_id = await _payment(conn)
    await service.start_checkout(conn, fake.client(), payment_id, daily_cap=CAP)
    [checkout] = await commands.open_commands(conn, payment_id)

    async with await connect(database_url) as replacer, await connect(database_url) as sender:
        async with replacer.transaction():
            await commands.open_attempt(replacer, payment_id, "test_card", daily_cap=CAP)
            send = asyncio.create_task(commands.claim(sender, checkout))
            for _ in range(100):
                if await _lock_waiters(conn):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("the send never waited on the attempt's row")
        with pytest.raises(Conflict, match="MONEY_COMMAND_NOT_OPEN"):
            await send

    # The other order: the replacement attempt is now sent, so a newer one must not win.
    [card_attempt] = await commands.open_commands(conn, payment_id)
    await commands.claim(conn, card_attempt)
    with pytest.raises(Conflict, match="PAYMENT_ATTEMPT_IN_FLIGHT"):
        async with conn.transaction():
            await commands.open_attempt(conn, payment_id, "checkout", daily_cap=CAP)
    assert (await _command(conn, card_attempt))["status"] == "pending"


async def test_a_checkout_the_buyer_approves_places_the_hold(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _payment(conn)

    order_id = await service.start_checkout(conn, fake.client(), payment_id, daily_cap=CAP)
    await service.complete_checkout(conn, fake.client(), payment_id, order_id)

    row = await _payment_row(conn, payment_id)
    assert row["state"] == "authorized"
    assert row["authorization_id"]


async def test_an_abandoned_checkout_is_replaced_by_a_new_attempt(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _payment(conn, "deposit")
    abandoned = await service.start_checkout(conn, fake.client(), payment_id, daily_cap=CAP)

    await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)

    assert (await _payment_row(conn, payment_id))["state"] == "captured"
    with pytest.raises(Conflict, match="PAYMENT_ATTEMPT_CLOSED"):
        await service.complete_checkout(conn, fake.client(), payment_id, abandoned)


async def test_a_refused_card_closes_the_attempt_and_the_guest_can_retry(conn: Conn) -> None:
    fake = FakePayPal()
    fake.refuse.add("orders")
    payment_id = await _payment(conn)

    with pytest.raises(service.PaymentRefused, match="PAYPAL_DECLINED"):
        await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)
    fake.refuse.clear()
    await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)

    assert (await _payment_row(conn, payment_id))["state"] == "authorized"


async def test_a_reauthorization_replaces_the_hold_the_capture_targets(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake)
    original = (await _payment_row(conn, payment_id))["authorization_id"]
    async with conn.transaction():
        reauth = await commands.record_command(conn, payment_id, "reauthorize", amount_minor=30000)
    await service.execute(conn, fake.client(), reauth["id"])

    async with conn.transaction():
        capture = await commands.record_command(conn, payment_id, "capture", amount_minor=30000)

    renewed = (await _payment_row(conn, payment_id))["authorization_id"]
    assert reauth["target_id"] == original
    assert capture["target_id"] == renewed != original


async def test_the_daily_cap_stops_new_payments_but_never_a_release(conn: Conn) -> None:
    fake = FakePayPal()
    workspace = await _workspace(conn)
    held = await _payment(conn, workspace=workspace)
    await service.pay_with_test_card(conn, fake.client(), held, TEST_CARD, daily_cap=2)
    second = await _payment(conn, workspace=workspace)
    await service.pay_with_test_card(conn, fake.client(), second, TEST_CARD, daily_cap=2)
    third = await _payment(conn, workspace=workspace)

    with pytest.raises(LimitReached, match="PAYPAL_DAILY_CAP_REACHED"):
        await service.pay_with_test_card(conn, fake.client(), third, TEST_CARD, daily_cap=2)
    async with conn.transaction():
        void = await commands.record_command(conn, held, "void", amount_minor=0)
    assert await service.execute(conn, fake.client(), void["id"]) == "succeeded"
    # Another workspace has its own allowance.
    elsewhere = await _payment(conn)
    await service.pay_with_test_card(conn, fake.client(), elsewhere, TEST_CARD, daily_cap=2)


async def test_payment_events_are_append_only(conn: Conn) -> None:
    payment_id = await _payment(conn)

    with pytest.raises(psycopg.errors.RaiseException, match="APPEND_ONLY"):
        await conn.execute(
            "update payment_events set kind = 'edited' where payment_id = %s", (payment_id,)
        )
    with pytest.raises(psycopg.errors.RaiseException, match="APPEND_ONLY"):
        await conn.execute("delete from payment_events where payment_id = %s", (payment_id,))


async def test_a_refused_resend_keeps_the_command_open_and_its_key(conn: Conn) -> None:
    """The first send may have refunded: PayPal refusing the resend proves nothing, and a read
    that does not find the refund cannot settle it, so it stays open, keeps its key and is
    not sent again (step 8b asks the operator)."""
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    fake.lose_first.add("refund")
    command = await _refund(conn, payment_id, 5000)
    await service.execute(conn, fake.client(), command["id"])
    fake.refuse.add("refund")

    first = await service.reconcile(conn, fake.client(), payment_id)
    again = await service.reconcile(conn, fake.client(), payment_id)

    row = await _command(conn, command["id"])
    assert first == again == {command["id"]: "unknown"}
    assert (row["status"], row["error_code"]) == ("unknown", "PAYPAL_DECLINED")
    assert row["request_id"] == command["request_id"]
    assert row["resend_refused_at"] is not None
    assert fake.posts("/refund") == 2  # the lost send and one refused resend, never a third
    # it still counts against the capture
    with pytest.raises(Conflict, match="REFUND_EXCEEDS_CAPTURED"):
        await _refund(conn, payment_id, 25001)


async def test_a_refused_resend_is_read_before_any_ask(conn: Conn) -> None:
    """PayPal took the first refund, lost the answer, then refuses the resend: the order shows
    the refund, so it is recorded, with no resend and nothing for the operator."""
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    fake.time_out_first.add("refund")
    command = await _refund(conn, payment_id, 5000)
    assert await service.execute(conn, fake.client(), command["id"]) == "unknown"
    fake.refuse.add("refund")

    outcome = await service.reconcile(conn, fake.client(), payment_id)

    assert outcome == {command["id"]: "succeeded"}
    assert fake.posts("/refund") == 1
    assert (await _payment_row(conn, payment_id))["refunded_minor"] == 5000


async def test_a_payment_is_refunded_twice_and_a_third_refund_above_its_capture_is_refused(
    conn: Conn,
) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    for amount in (10000, 15000):
        command = await _refund(conn, payment_id, amount)
        assert await service.execute(conn, fake.client(), command["id"]) == "succeeded"

    with pytest.raises(Conflict, match="REFUND_EXCEEDS_CAPTURED"):
        await _refund(conn, payment_id, 5001)
    await _refund(conn, payment_id, 5000)  # exactly what is left still fits
    row = await _payment_row(conn, payment_id)
    assert (row["captured_minor"], row["refunded_minor"]) == (30000, 25000)
    assert fake.posts("/refund") == 2  # the third was refused before PayPal


async def _row_lock_waiters(conn: Conn) -> int:
    row = await (
        await conn.execute(
            "select count(*) as n from pg_stat_activity where wait_event_type = 'Lock'"
            " and query like '%%from payments where id%%for update%%'"
        )
    ).fetchone()
    return int(row["n"]) if row else 0


async def test_two_concurrent_refunds_never_exceed_the_capture(
    conn: Conn, database_url: str
) -> None:
    """Both wait on the payment's row; whichever records second sees the first as open."""
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")

    async def refund(url: str) -> object:
        async with await connect(url) as other:
            return await _refund(other, payment_id, 20000)

    async with await connect(database_url) as holder:
        async with holder.transaction():
            await holder.execute("select 1 from payments where id = %s for update", (payment_id,))
            racing = [asyncio.create_task(refund(database_url)) for _ in range(2)]
            for _ in range(100):
                if await _row_lock_waiters(conn) == 2:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("the refunds never waited on the payment's row")
        results = await asyncio.gather(*racing, return_exceptions=True)

    refused = [r for r in results if isinstance(r, Conflict)]
    assert len(refused) == 1
    assert refused[0].code == "REFUND_EXCEEDS_CAPTURED"


async def test_an_unknown_open_is_resolved_from_its_order_before_any_resend(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _payment(conn, "deposit")
    order_id = await service.start_checkout(conn, fake.client(), payment_id, daily_cap=CAP)
    fake.time_out_first.add("capture")
    with pytest.raises(PayPalUnknownOutcome):
        await service.complete_checkout(conn, fake.client(), payment_id, order_id)
    before = len(fake.calls)

    outcome = await service.reconcile(conn, fake.client(), payment_id)

    [command_id] = outcome
    assert outcome[command_id] == "succeeded"
    assert fake.calls[before:] == [f"GET /v2/checkout/orders/{order_id}"]
    row = await _payment_row(conn, payment_id)
    assert (row["state"], row["order_id"], row["captured_minor"]) == ("captured", order_id, 30000)


async def test_an_unknown_refund_is_found_in_the_order_by_its_invoice_id(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    earlier = await _refund(conn, payment_id, 1000)
    await service.execute(conn, fake.client(), earlier["id"])
    fake.time_out_first.add("refund")
    command = await _refund(conn, payment_id, 4000)
    await service.execute(conn, fake.client(), command["id"])

    outcome = await service.reconcile(conn, fake.client(), payment_id)

    assert outcome == {command["id"]: "succeeded"}
    assert fake.posts("/refund") == 2
    assert fake.calls[-1].startswith("GET /v2/checkout/orders/")
    assert (await _payment_row(conn, payment_id))["refunded_minor"] == 5000


async def test_a_pending_refund_stays_open_until_paypal_completes_it(conn: Conn) -> None:
    fake = FakePayPal()
    fake.refund_status = "PENDING"
    payment_id = await _paid(conn, fake, "deposit")
    command = await _refund(conn, payment_id, 20000)

    assert await service.execute(conn, fake.client(), command["id"]) == "unknown"
    assert await service.reconcile(conn, fake.client(), payment_id) == {command["id"]: "unknown"}
    assert (await _payment_row(conn, payment_id))["refunded_minor"] == 0
    with pytest.raises(Conflict, match="REFUND_EXCEEDS_CAPTURED"):
        await _refund(conn, payment_id, 10001)  # the pending one still counts
    fake.settle_refunds()

    assert await service.reconcile(conn, fake.client(), payment_id) == {command["id"]: "succeeded"}
    row = await _command(conn, command["id"])
    assert row["status"] == "succeeded"
    assert (await _payment_row(conn, payment_id))["refunded_minor"] == 20000
    assert fake.posts("/refund") == 1


async def test_a_test_card_payment_keeps_its_order_id(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    [command] = await (
        await conn.execute(
            "select order_id from money_commands where payment_id = %s", (payment_id,)
        )
    ).fetchall()
    assert command["order_id"] is not None
    assert (await _payment_row(conn, payment_id))["order_id"] == command["order_id"]
    # an answer we cannot record still names its order, so the read can find it
    fake.card_status = "PENDING"
    held = await _payment(conn)
    with pytest.raises(PayPalUnknownOutcome):
        await service.pay_with_test_card(conn, fake.client(), held, TEST_CARD, daily_cap=CAP)
    [unknown] = await commands.open_commands(conn, held)
    assert (await _command(conn, unknown))["order_id"] in fake.orders


async def test_a_refund_past_the_window_is_refused_before_paypal_is_called(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake, "deposit")
    captured_at = (await _payment_row(conn, payment_id))["captured_at"]
    assert isinstance(captured_at, datetime)

    with pytest.raises(Conflict, match="REFUND_WINDOW_CLOSED"):
        await _refund(conn, payment_id, 1000, now=captured_at + timedelta(days=180, seconds=1))
    assert fake.posts("/refund") == 0
    await _refund(conn, payment_id, 1000, now=captured_at + timedelta(days=180))


async def test_a_hold_is_reauthorized_again_but_one_at_a_time(conn: Conn) -> None:
    fake = FakePayPal()
    payment_id = await _paid(conn, fake)
    for _ in range(2):
        async with conn.transaction():
            reauth = await commands.record_command(
                conn, payment_id, "reauthorize", amount_minor=30000
            )
        assert await service.execute(conn, fake.client(), reauth["id"]) == "succeeded"
    async with conn.transaction():
        await commands.record_command(conn, payment_id, "reauthorize", amount_minor=30000)

    with pytest.raises(psycopg.errors.UniqueViolation):
        async with conn.transaction():
            await commands.record_command(conn, payment_id, "reauthorize", amount_minor=30000)
