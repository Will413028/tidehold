"""Trips, guests and their deposit and balance payments through HTTP and the worker (plan step 6
acceptance)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import TRIP, post_trip, trip_starting
from tidehold.bootstrap import worker
from tidehold.bootstrap.app import create_app
from tidehold.platform.db import Conn
from tidehold.platform.settings import Settings
from tidehold.trips import store


async def _tick(conn: Conn, paypal: FakePayPal, now: datetime) -> None:
    await worker.tick(conn, worker.handlers(paypal.client()), now)


async def _invite(operator: AsyncClient, trip: dict[str, Any], name: str = "Ana") -> str:
    created = (await post_trip(operator, trip)).json()
    invited = await operator.post(f"/api/trips/{created['id']}/guests", json={"name": name})
    token: str = invited.json()["guests"][-1]["token"]
    return token


async def test_a_guest_gets_its_balance_payment_at_invitation(
    operator: AsyncClient, client: AsyncClient
) -> None:
    starts_on = datetime.now(ZoneInfo("Asia/Manila")).date() + timedelta(days=60)
    token = await _invite(operator, trip_starting(starts_on))

    balance = (await client.get(f"/api/guest/{token}")).json()["balance"]

    # due 30 days before the first trip day, a local date in Asia/Manila
    assert balance == {
        "due_on": (starts_on - timedelta(days=30)).isoformat(),
        "amount_minor": 30000,
        "payment": {
            "state": "pending",
            "amount_minor": 30000,
            "due_minor": 30000,
            "captured_minor": 0,
            "refunded_minor": 0,
        },
    }


async def test_the_balance_is_captured_when_the_guest_pays(
    operator: AsyncClient, client: AsyncClient, paypal: FakePayPal
) -> None:
    by_card = await _invite(operator, TRIP)
    by_checkout = await _invite(operator, TRIP, name="Ben")

    card = (await client.post(f"/api/guest/{by_card}/balance/test-card")).json()["balance"]
    order = (await client.post(f"/api/guest/{by_checkout}/balance/checkout")).json()["order_id"]
    done = await client.post(f"/api/guest/{by_checkout}/balance/checkout/{order}")

    paid = {
        "state": "captured",
        "amount_minor": 30000,
        "due_minor": 0,
        "captured_minor": 30000,
        "refunded_minor": 0,
    }
    assert card["payment"] == paid
    assert done.json()["balance"]["payment"] == paid
    assert {o["intent"] for o in paypal.orders.values()} == {"CAPTURE"}


@pytest.mark.parametrize("days", [-1, 121, "30", 30.0])
async def test_balance_due_days_outside_0_to_120_are_refused(
    operator: AsyncClient, days: object
) -> None:
    response = await post_trip(operator, balance_due_days=days)

    assert (response.status_code, response.json()) == (400, {"detail": "INVALID_REQUEST"})


@pytest.mark.parametrize("days", [0, 120])
async def test_balance_due_days_at_their_bounds_are_accepted(
    operator: AsyncClient, days: int
) -> None:
    response = await post_trip(operator, balance_due_days=days)

    assert (response.status_code, response.json()["balance_due_days"]) == (201, days)


async def test_the_deposit_capture_is_recorded_with_its_money_command(
    operator: AsyncClient, client: AsyncClient, conn: Conn
) -> None:
    token = await _invite(operator, TRIP)

    paid = (await client.post(f"/api/guest/{token}/deposit/test-card")).json()["deposit"]

    guest = await store.guest_by_token(conn, token)
    commands = await (
        await conn.execute(
            "select kind, method, status, amount_minor from money_commands where payment_id = %s",
            (guest["deposit_id"],),
        )
    ).fetchall()
    events = await (
        await conn.execute(
            "select kind from payment_events where payment_id = %s order by id",
            (guest["deposit_id"],),
        )
    ).fetchall()
    assert paid == {
        "state": "captured",
        "amount_minor": 5000,
        "due_minor": 0,
        "captured_minor": 5000,
        "refunded_minor": 0,
    }
    assert [dict(c) for c in commands] == [
        {"kind": "open", "method": "test_card", "status": "succeeded", "amount_minor": 5000}
    ]
    assert [e["kind"] for e in events] == ["payment_created", "open_succeeded"]


async def test_a_deposit_paid_through_paypal_checkout_is_captured(
    operator: AsyncClient, client: AsyncClient
) -> None:
    token = await _invite(operator, TRIP)

    order = (await client.post(f"/api/guest/{token}/deposit/checkout")).json()["order_id"]
    done = await client.post(f"/api/guest/{token}/deposit/checkout/{order}")

    assert done.json()["deposit"]["state"] == "captured"


async def test_an_unknown_payment_is_reconciled_by_the_due_action(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    token = await _invite(operator, TRIP)
    paypal.time_out_first.add("orders")

    lost = await client.post(f"/api/guest/{token}/deposit/test-card")
    scheduled = await (
        await conn.execute("select kind, state from due_actions where kind = 'reconcile_payment'")
    ).fetchall()
    await _tick(conn, paypal, datetime.now(UTC) + timedelta(minutes=2))

    assert (lost.status_code, lost.json()) == (503, {"detail": "PAYPAL_OUTCOME_UNKNOWN"})
    assert [dict(s) for s in scheduled] == [{"kind": "reconcile_payment", "state": "pending"}]
    assert (await client.get(f"/api/guest/{token}")).json()["deposit"]["state"] == "captured"
    assert len([c for c in paypal.calls if c == "POST /v2/checkout/orders"]) == 2  # one resend
    assert paypal.moved("") == 1  # one order took effect


async def test_judge_flow_and_workspace_isolation(
    operator: AsyncClient,
    other_operator: AsyncClient,
    client: AsyncClient,
) -> None:
    """Ported from dive-trip-agent@8065eba tests/test_deposits_http.py: the operator sets up a
    trip, a guest pays deposit and balance, and another workspace sees none of it."""
    trip = (await post_trip(operator)).json()
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Judge"})
    token = invited.json()["guests"][0]["token"]

    order = (await client.post(f"/api/guest/{token}/deposit/checkout")).json()["order_id"]
    await client.post(f"/api/guest/{token}/deposit/checkout/{order}")
    await client.post(f"/api/guest/{token}/balance/test-card")
    seen = (await operator.get(f"/api/trips/{trip['id']}")).json()

    assert [(g["name"], g["deposit_state"], g["balance_state"]) for g in seen["guests"]] == [
        ("Judge", "captured", "captured")
    ]
    assert (await other_operator.get(f"/api/trips/{trip['id']}")).status_code == 404
    assert (await other_operator.get("/api/trips")).json() == []
    stranger_invite = await other_operator.post(
        f"/api/trips/{trip['id']}/guests", json={"name": "Intruder"}
    )
    assert stranger_invite.status_code == 404
    # A guest link is not an operator session.
    assert (await client.get(f"/api/trips/{trip['id']}")).status_code == 401


async def test_a_workspace_cannot_create_trips_beyond_its_cap(
    operator: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_TRIPS_PER_WORKSPACE", 1)
    await post_trip(operator)

    second = await post_trip(operator)

    assert (second.status_code, second.json()) == (429, {"detail": "TRIP_CAP_REACHED"})


async def test_a_trip_takes_a_bounded_number_of_guests(
    operator: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_GUESTS_PER_TRIP", 1)
    trip = (await post_trip(operator)).json()
    url = f"/api/trips/{trip['id']}/guests"
    await operator.post(url, json={"name": "Ana"})

    other = await operator.post(url, json={"name": "Ben"})

    assert (other.status_code, other.json()) == (429, {"detail": "GUEST_CAP_REACHED"})


async def test_a_guest_name_is_taken_once_per_trip(operator: AsyncClient) -> None:
    trip = (await post_trip(operator)).json()
    url = f"/api/trips/{trip['id']}/guests"
    await operator.post(url, json={"name": "Ana"})

    again = await operator.post(url, json={"name": "Ana"})

    assert (again.status_code, again.json()) == (409, {"detail": "GUEST_NAME_TAKEN"})


@pytest.mark.parametrize("amount", ["5000", 5000.0, 0, -1])
async def test_money_inputs_are_strict_and_refusals_do_not_echo_them(
    operator: AsyncClient, amount: object
) -> None:
    response = await post_trip(operator, deposit_minor=amount)

    assert (response.status_code, response.json()) == (400, {"detail": "INVALID_REQUEST"})


async def test_without_paypal_credentials_payments_are_unavailable(
    database_url: str, operator: AsyncClient
) -> None:
    token = await _invite(operator, TRIP)
    bare = create_app(Settings(database_url=database_url, public_origin="http://test"))
    transport = ASGITransport(app=bare)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": "http://test"}
    ) as guest:
        paid = await guest.post(f"/api/guest/{token}/deposit/test-card")
        balance = await guest.post(f"/api/guest/{token}/balance/test-card")
        config = await guest.get("/api/paypal/config")

    assert (paid.status_code, paid.json()) == (501, {"detail": "PAYPAL_NOT_CONFIGURED"})
    assert (balance.status_code, balance.json()) == (501, {"detail": "PAYPAL_NOT_CONFIGURED"})
    assert config.json() == {"client_id": None}


async def test_the_trip_timeline_lists_each_guests_money_events(
    operator: AsyncClient, client: AsyncClient
) -> None:
    trip = (await post_trip(operator)).json()
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ana"})
    await client.post(f"/api/guest/{invited.json()['guests'][0]['token']}/deposit/test-card")

    events = (await operator.get(f"/api/trips/{trip['id']}/timeline")).json()

    assert [(e["guest"], e["purpose"], e["kind"]) for e in events] == [
        ("Ana", "deposit", "payment_created"),
        ("Ana", "balance", "payment_created"),
        ("Ana", "deposit", "open_succeeded"),
    ]
