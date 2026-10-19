"""Trips and their invited guests. Each guest owns a deposit payment from the invitation and
a balance payment once the balance hold is requested (`holds` decides when)."""

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.money import commands
from tidehold.platform.db import Conn, insert_returning
from tidehold.platform.errors import Conflict, Invalid, LimitReached, NotFound
from tidehold.policy import versions

# Longer trips cannot settle inside one PayPal authorization (deposit/balance ADR).
MAX_TRIP_DAYS = 10
# Bounds on what one anonymous workspace can create on the shared sandbox (threat model).
MAX_TRIPS_PER_WORKSPACE = 20
MAX_GUESTS_PER_TRIP = 12


@dataclass(frozen=True)
class NewTrip:
    name: str
    starts_on: date
    days: int
    site_lat: float
    site_lon: float
    warning_source: str
    warning_areas: list[str]
    deposit_minor: int
    balance_minor: int
    policy_version_id: UUID
    policy_product: str | None = None


def ends_on(starts_on: date, days: int) -> date:
    return starts_on + timedelta(days=days - 1)


def _check_binding(version: DictRow, trip: NewTrip) -> None:
    """The trip's warning source is the one its policy's warning conditions name, and a
    version with several products says which one the trip is."""
    source = version["warning_source"]
    if source is not None and source != trip.warning_source:
        raise Invalid("WARNING_SOURCE_MISMATCH")
    products = versions.products(version)
    if trip.policy_product is not None and trip.policy_product not in products:
        raise Invalid("POLICY_PRODUCT_UNKNOWN")
    if trip.policy_product is None and len(products) > 1:
        raise Invalid("POLICY_PRODUCT_REQUIRED")


async def create_trip(conn: Conn, workspace_id: UUID, trip: NewTrip) -> UUID:
    _check_binding(await versions.version(conn, workspace_id, trip.policy_version_id), trip)
    async with conn.transaction():
        await conn.execute("select 1 from workspaces where id = %s for update", (workspace_id,))
        count = await (
            await conn.execute(
                "select count(*) as n from trips where workspace_id = %s", (workspace_id,)
            )
        ).fetchone()
        if count is not None and count["n"] >= MAX_TRIPS_PER_WORKSPACE:
            raise LimitReached("TRIP_CAP_REACHED")
        row = await insert_returning(
            conn,
            "insert into trips (workspace_id, name, starts_on, days, site_lat, site_lon,"
            " warning_source, warning_areas, deposit_minor, balance_minor, policy_version_id,"
            " policy_product) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " returning id",
            (
                workspace_id,
                trip.name,
                trip.starts_on,
                trip.days,
                trip.site_lat,
                trip.site_lon,
                trip.warning_source,
                trip.warning_areas,
                trip.deposit_minor,
                trip.balance_minor,
                trip.policy_version_id,
                trip.policy_product,
            ),
        )
    trip_id: UUID = row["id"]
    return trip_id


async def trip(conn: Conn, workspace_id: UUID, trip_id: UUID) -> DictRow:
    row = await (
        await conn.execute(
            "select * from trips where id = %s and workspace_id = %s", (trip_id, workspace_id)
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return row


async def trips(conn: Conn, workspace_id: UUID) -> list[DictRow]:
    return await (
        await conn.execute(
            "select * from trips where workspace_id = %s order by starts_on, created_at",
            (workspace_id,),
        )
    ).fetchall()


GUEST_PAYMENTS = (
    "select g.id, g.name, g.token, g.trip_id, g.workspace_id,"
    " d.id as deposit_id, d.state as deposit_state, d.amount_minor as deposit_minor,"
    " d.captured_minor as deposit_captured_minor, d.refunded_minor as deposit_refunded_minor,"
    " b.id as balance_id, b.state as balance_state, b.amount_minor as balance_minor,"
    " b.captured_minor as balance_captured_minor"
    " from guests g join payments d on d.id = g.deposit_payment_id"
    " left join payments b on b.id = g.balance_payment_id"
)


async def guests(conn: Conn, trip_id: UUID) -> list[DictRow]:
    return await (
        await conn.execute(
            GUEST_PAYMENTS + " where g.trip_id = %s order by g.created_at, g.id", (trip_id,)
        )
    ).fetchall()


async def guest_by_token(conn: Conn, token: str) -> DictRow:
    row = await (await conn.execute(GUEST_PAYMENTS + " where g.token = %s", (token,))).fetchone()
    if row is None:
        raise NotFound
    return row


async def invite(conn: Conn, workspace_id: UUID, trip_id: UUID, name: str) -> UUID:
    """A guest with their own link and a deposit payment; a balance payment too when the trip's
    balance hold is already open."""
    async with conn.transaction():
        row = await (
            await conn.execute(
                "select deposit_minor, balance_requested_at from trips"
                " where id = %s and workspace_id = %s for update",
                (trip_id, workspace_id),
            )
        ).fetchone()
        if row is None:
            raise NotFound
        count = await (
            await conn.execute("select count(*) as n from guests where trip_id = %s", (trip_id,))
        ).fetchone()
        if count is not None and count["n"] >= MAX_GUESTS_PER_TRIP:
            raise LimitReached("GUEST_CAP_REACHED")
        if await (
            await conn.execute(
                "select 1 from guests where trip_id = %s and name = %s", (trip_id, name)
            )
        ).fetchone():
            raise Conflict("GUEST_NAME_TAKEN")
        deposit = await commands.create_payment(
            conn, workspace_id=workspace_id, purpose="deposit", amount_minor=row["deposit_minor"]
        )
        guest = await insert_returning(
            conn,
            "insert into guests (workspace_id, trip_id, name, token, deposit_payment_id)"
            " values (%s, %s, %s, %s, %s) returning id",
            (workspace_id, trip_id, name, secrets.token_urlsafe(24), deposit),
        )
        if row["balance_requested_at"] is not None:
            await _open_balances(conn, trip_id)
    guest_id: UUID = guest["id"]
    return guest_id


async def open_balances(conn: Conn, trip_id: UUID, now: datetime) -> int:
    """Open the trip's balance hold: mark it requested and give every guest a balance payment.
    Idempotent; returns how many payments it created."""
    async with conn.transaction():
        await conn.execute(
            "update trips set balance_requested_at = coalesce(balance_requested_at, %s)"
            " where id = %s",
            (now, trip_id),
        )
        return await _open_balances(conn, trip_id)


async def _open_balances(conn: Conn, trip_id: UUID) -> int:
    pending = await (
        await conn.execute(
            "select g.id, g.workspace_id, t.balance_minor from guests g"
            " join trips t on t.id = g.trip_id"
            " where g.trip_id = %s and g.balance_payment_id is null for update of g",
            (trip_id,),
        )
    ).fetchall()
    for guest in pending:
        payment = await commands.create_payment(
            conn,
            workspace_id=guest["workspace_id"],
            purpose="balance",
            amount_minor=guest["balance_minor"],
        )
        await conn.execute(
            "update guests set balance_payment_id = %s where id = %s", (payment, guest["id"])
        )
    return len(pending)


async def timeline(conn: Conn, workspace_id: UUID, trip_id: UUID) -> list[DictRow]:
    """Every money event of the trip's guests, oldest first (the audit timeline)."""
    await trip(conn, workspace_id, trip_id)
    return await (
        await conn.execute(
            "select e.created_at as at, g.name as guest, p.purpose, e.kind, e.detail"
            " from guests g join payments p"
            "   on p.id in (g.deposit_payment_id, g.balance_payment_id)"
            " join payment_events e on e.payment_id = p.id"
            " where g.trip_id = %s order by e.id",
            (trip_id,),
        )
    ).fetchall()
