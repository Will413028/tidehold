"""Trips, guests and balance holds through HTTP and the worker (plan step 6 acceptance)."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import TRIP, trip_starting
from tidehold.bootstrap import worker
from tidehold.bootstrap.app import create_app
from tidehold.platform.db import Conn
from tidehold.platform.settings import Settings
from tidehold.trips import store

# TRIP starts 2026-12-05 for 3 days: its balance opens at T-(29 - 3) = 2026-11-09 00:00 UTC.
OPENS = datetime(2026, 11, 9, tzinfo=UTC)


async def _tick(conn: Conn, paypal: FakePayPal, now: datetime) -> None:
    await worker.tick(conn, worker.handlers(paypal.client()), now)


async def _invite(operator: AsyncClient, trip: dict[str, Any], name: str = "Ana") -> str:
    created = (await operator.post("/api/trips", json=trip)).json()
    invited = await operator.post(f"/api/trips/{created['id']}/guests", json={"name": name})
    token: str = invited.json()["guests"][-1]["token"]
    return token


async def test_the_balance_hold_opens_at_t_minus_29_minus_n(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    token = await _invite(operator, TRIP)

    await _tick(conn, paypal, OPENS - timedelta(seconds=1))
    early = await client.post(f"/api/guest/{token}/balance/test-card")
    before = (await client.get(f"/api/guest/{token}")).json()["balance"]
    await _tick(conn, paypal, OPENS)
    opened = (await client.get(f"/api/guest/{token}")).json()["balance"]
    held = (await client.post(f"/api/guest/{token}/balance/test-card")).json()["balance"]

    assert (early.status_code, early.json()) == (409, {"detail": "BALANCE_NOT_OPEN_YET"})
    assert before["payment"] is None
    assert before["opens_at"] == "2026-11-09T00:00:00Z"
    assert opened["payment"]["state"] == "pending"
    assert held["payment"] == {"state": "authorized", "amount_minor": 30000, "captured_minor": 0}


async def test_a_trip_booked_inside_the_window_opens_at_the_next_tick(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    token = await _invite(operator, trip_starting(date(2026, 11, 20)))

    await _tick(conn, paypal, datetime(2026, 11, 1, 12, tzinfo=UTC))

    assert (await client.get(f"/api/guest/{token}")).json()["balance"]["payment"] is not None


async def test_a_guest_invited_after_the_balance_opened_gets_a_balance_payment(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = (await operator.post("/api/trips", json=TRIP)).json()
    await _tick(conn, paypal, OPENS)

    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})

    assert invited.json()["guests"][0]["balance_state"] == "pending"


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
    assert paid == {"state": "captured", "amount_minor": 5000, "captured_minor": 5000}
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
    conn: Conn,
    paypal: FakePayPal,
) -> None:
    """Ported from dive-trip-agent@8065eba tests/test_deposits_http.py: the operator sets up a
    trip, a guest pays deposit and balance, and another workspace sees none of it."""
    trip = (await operator.post("/api/trips", json=TRIP)).json()
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Judge"})
    token = invited.json()["guests"][0]["token"]

    order = (await client.post(f"/api/guest/{token}/deposit/checkout")).json()["order_id"]
    await client.post(f"/api/guest/{token}/deposit/checkout/{order}")
    await _tick(conn, paypal, OPENS)
    await client.post(f"/api/guest/{token}/balance/test-card")
    seen = (await operator.get(f"/api/trips/{trip['id']}")).json()

    assert [(g["name"], g["deposit_state"], g["balance_state"]) for g in seen["guests"]] == [
        ("Judge", "captured", "authorized")
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
    await operator.post("/api/trips", json=TRIP)

    second = await operator.post("/api/trips", json=TRIP)

    assert (second.status_code, second.json()) == (429, {"detail": "TRIP_CAP_REACHED"})


async def test_a_trip_takes_a_bounded_number_of_guests(
    operator: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MAX_GUESTS_PER_TRIP", 1)
    trip = (await operator.post("/api/trips", json=TRIP)).json()
    url = f"/api/trips/{trip['id']}/guests"
    await operator.post(url, json={"name": "Ana"})

    other = await operator.post(url, json={"name": "Ben"})

    assert (other.status_code, other.json()) == (429, {"detail": "GUEST_CAP_REACHED"})


async def test_a_guest_name_is_taken_once_per_trip(operator: AsyncClient) -> None:
    trip = (await operator.post("/api/trips", json=TRIP)).json()
    url = f"/api/trips/{trip['id']}/guests"
    await operator.post(url, json={"name": "Ana"})

    again = await operator.post(url, json={"name": "Ana"})

    assert (again.status_code, again.json()) == (409, {"detail": "GUEST_NAME_TAKEN"})


@pytest.mark.parametrize("amount", ["5000", 5000.0, 0, -1])
async def test_money_inputs_are_strict_and_refusals_do_not_echo_them(
    operator: AsyncClient, amount: object
) -> None:
    response = await operator.post("/api/trips", json={**TRIP, "deposit_minor": amount})

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
        config = await guest.get("/api/paypal/config")

    assert (paid.status_code, paid.json()) == (503, {"detail": "PAYPAL_NOT_CONFIGURED"})
    assert config.json() == {"client_id": None}
