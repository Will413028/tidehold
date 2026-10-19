"""The money engine against a real database and an in-memory PayPal.

Ported from dive-trip-agent@8065eba `tests/test_deposits_{transactions,service,join}.py`,
keyed by payment instead of booking and diver.
"""

import asyncio
from uuid import UUID

import psycopg
import pytest

from tests.integration.fake_paypal import FakePayPal
from tidehold.identity.sessions import new_workspace
from tidehold.money import commands, service
from tidehold.money.commands import Purpose
from tidehold.money.paypal import TEST_CARD, PayPalUnknownOutcome
from tidehold.platform.db import Conn, connect
from tidehold.platform.errors import Conflict, LimitReached

CAP = 30


async def _workspace(conn: Conn) -> UUID:
    workspace_id, _ = await new_workspace(conn)
    return workspace_id


async def _payment(
    conn: Conn, purpose: Purpose = "balance", amount: int = 30000, workspace: UUID | None = None
) -> UUID:
    return await commands.create_payment(
        conn, workspace_id=workspace or await _workspace(conn), purpose=purpose, amount_minor=amount
    )


async def _paid(conn: Conn, fake: FakePayPal, purpose: Purpose = "balance") -> UUID:
    payment_id = await _payment(conn, purpose)
    await service.pay_with_test_card(conn, fake.client(), payment_id, TEST_CARD, daily_cap=CAP)
    return payment_id


async def _payment_row(conn: Conn, payment_id: UUID) -> dict[str, object]:
    return dict(await commands.payment(conn, payment_id))


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
    async with conn.transaction():
        command = await commands.record_command(conn, payment_id, "refund", amount_minor=5000)

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
    """The first send may have captured: PayPal refusing the resend proves nothing."""
    fake = FakePayPal()
    payment_id = await _paid(conn, fake)
    fake.time_out_first.add("capture")
    async with conn.transaction():
        command = await commands.record_command(conn, payment_id, "capture", amount_minor=30000)
    await service.execute(conn, fake.client(), command["id"])
    fake.refuse.add("capture")

    outcome = await service.reconcile(conn, fake.client(), payment_id)

    assert outcome == {command["id"]: "unknown"}
    assert (await _command(conn, command["id"]))["error_code"] == "PAYPAL_DECLINED"
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with conn.transaction():
            await commands.record_command(conn, payment_id, "void", amount_minor=0)


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
