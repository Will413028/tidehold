"""What a guest's own link shows and does: the trip, the deposit (captured through PayPal at
booking, D3) and the balance (captured when the guest pays it, payable from the invitation and
due before the trip, D18). Guest access class: the unguessable token is the only credential."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Path, Request
from psycopg.rows import DictRow
from pydantic import BaseModel

from tidehold.holds.schedule import balance_due_at
from tidehold.money import service
from tidehold.money.paypal import TEST_CARD, PayPalClient
from tidehold.platform.db import Conn, connection
from tidehold.platform.errors import Conflict, NotConfigured
from tidehold.trips import store

router = APIRouter(prefix="/api/guest/{token}")
public_router = APIRouter(prefix="/api")

Db = Annotated[Conn, Depends(connection)]
Purpose = Literal["deposit", "balance"]
OrderId = Annotated[str, Path(pattern=r"^[A-Z0-9]{8,32}$")]


class Payment(BaseModel):
    state: str
    amount_minor: int
    captured_minor: int


class Balance(BaseModel):
    # the local date in the trip's time zone
    due_on: date
    amount_minor: int
    payment: Payment | None  # None only for a guest invited before migration 0015


class GuestView(BaseModel):
    """No workspace, payment or PayPal ids: only what this guest needs."""

    guest_name: str
    trip_name: str
    starts_on: date
    ends_on: date
    days: int
    deposit: Payment
    balance: Balance


class Order(BaseModel):
    order_id: str


def _due_on(trip: DictRow, guest: DictRow) -> date:
    due = balance_due_at(trip, guest["created_at"])
    return due.astimezone(ZoneInfo(trip["timezone"])).date()


async def _view(conn: Conn, token: str) -> GuestView:
    guest = await store.guest_by_token(conn, token)
    trip = await store.trip(conn, guest["workspace_id"], guest["trip_id"])
    balance = None
    if guest["balance_id"] is not None:
        balance = Payment(
            state=guest["balance_state"],
            amount_minor=guest["balance_minor"],
            captured_minor=guest["balance_captured_minor"],
        )
    return GuestView(
        guest_name=guest["name"],
        trip_name=trip["name"],
        starts_on=trip["starts_on"],
        ends_on=store.ends_on(trip["starts_on"], trip["days"]),
        days=trip["days"],
        deposit=Payment(
            state=guest["deposit_state"],
            amount_minor=guest["deposit_minor"],
            captured_minor=guest["deposit_captured_minor"],
        ),
        balance=Balance(
            due_on=_due_on(trip, guest),
            amount_minor=trip["balance_minor"],
            payment=balance,
        ),
    )


async def _payment(conn: Conn, token: str, purpose: Purpose) -> UUID:
    guest = await store.guest_by_token(conn, token)
    trip = await store.trip(conn, guest["workspace_id"], guest["trip_id"])
    if trip["cancelled_at"] is not None:
        raise Conflict("TRIP_CANCELLED")
    payment_id: UUID | None = guest["deposit_id"] if purpose == "deposit" else guest["balance_id"]
    if payment_id is None:
        raise Conflict("NO_BALANCE_PAYMENT")
    return payment_id


def _paypal(request: Request) -> PayPalClient:
    client: PayPalClient | None = request.app.state.paypal
    if client is None:
        raise NotConfigured("PAYPAL_NOT_CONFIGURED")
    return client


def _cap(request: Request) -> int:
    cap: int = request.app.state.settings.paypal_daily_open_cap
    return cap


@router.get("")
async def guest_view(token: str, conn: Db) -> GuestView:
    return await _view(conn, token)


@router.post("/{purpose}/checkout")
async def start_checkout(token: str, purpose: Purpose, request: Request, conn: Db) -> Order:
    """The order PayPal's checkout buttons approve (JS SDK)."""
    payment_id = await _payment(conn, token, purpose)
    order_id = await service.start_checkout(
        conn, _paypal(request), payment_id, daily_cap=_cap(request)
    )
    return Order(order_id=order_id)


@router.post("/{purpose}/checkout/{order_id}")
async def complete_checkout(
    token: str, purpose: Purpose, order_id: OrderId, request: Request, conn: Db
) -> GuestView:
    payment_id = await _payment(conn, token, purpose)
    await service.complete_checkout(conn, _paypal(request), payment_id, order_id)
    return await _view(conn, token)


@router.post("/{purpose}/test-card")
async def pay_with_test_card(token: str, purpose: Purpose, request: Request, conn: Db) -> GuestView:
    """Sandbox fallback when PayPal's checkout is unavailable: the server's test card."""
    payment_id = await _payment(conn, token, purpose)
    await service.pay_with_test_card(
        conn, _paypal(request), payment_id, TEST_CARD, daily_cap=_cap(request)
    )
    return await _view(conn, token)


class PayPalConfig(BaseModel):
    client_id: str | None  # public: the JS SDK needs it in the browser


@public_router.get("/paypal/config")
async def paypal_config(request: Request) -> PayPalConfig:
    return PayPalConfig(client_id=request.app.state.paypal_client_id)
