"""Operator routes for trips and invited guests (workspace access class)."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, status
from psycopg.rows import DictRow
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection
from tidehold.trips import store

router = APIRouter(prefix="/api")

Workspace = Annotated[UUID, Depends(current_workspace)]
Db = Annotated[Conn, Depends(connection)]
AreaCode = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,24}$")]
# Money inputs are strict: "300" or 300.0 is refused rather than coerced.
Minor = Annotated[int, Field(strict=True, ge=1, le=10_000_000)]
Cap = Annotated[int, Field(strict=True, ge=0, le=10_000_000)]


def _zone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("unknown time zone") from error
    return name


TimeZone = Annotated[str, StringConstraints(min_length=1, max_length=64), AfterValidator(_zone)]


class NewTrip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    starts_on: date
    days: int = Field(ge=1, le=store.MAX_TRIP_DAYS)
    site_lat: float = Field(ge=-90, le=90)
    site_lon: float = Field(ge=-180, le=180)
    warning_source: Literal["pagasa", "nws", "meteoalarm", "jma"]
    warning_areas: list[AreaCode] = Field(min_length=1, max_length=20)
    deposit_minor: Minor
    balance_minor: Minor
    policy_version_id: UUID
    policy_product: str | None = Field(default=None, min_length=1, max_length=200)
    # an IANA name: trip days are local dates (step 8a)
    timezone: TimeZone
    # the most the trip's settlements may return without the operator (step 8a)
    mandate_guest_minor: Cap
    mandate_trip_minor: Cap
    # the balance falls due this many days before the first local trip day (D18, D20)
    balance_due_days: int = Field(default=30, strict=True, ge=0, le=120)


class NewGuest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)


class Guest(BaseModel):
    id: UUID
    name: str
    token: str
    deposit_state: str
    balance_state: str | None
    # over both payments: what PayPal captured and what was refunded of it
    paid_minor: int
    refunded_minor: int
    # the operator released the guest from an unpaid balance (D21)
    released: bool


def _guest(g: DictRow) -> Guest:
    return Guest(
        id=g["id"],
        name=g["name"],
        token=g["token"],
        deposit_state=g["deposit_state"],
        balance_state=g["balance_state"],
        paid_minor=g["deposit_captured_minor"] + g["balance_captured_minor"],
        refunded_minor=g["deposit_refunded_minor"] + g["balance_refunded_minor"],
        released=g["released_at"] is not None,
    )


class Trip(BaseModel):
    id: UUID
    name: str
    starts_on: date
    ends_on: date
    days: int
    site_lat: float
    site_lon: float
    warning_source: str
    warning_areas: list[str]
    deposit_minor: int
    balance_minor: int
    balance_due_days: int
    policy_version_id: UUID
    policy_product: str | None
    timezone: str
    mandate_guest_minor: int
    mandate_trip_minor: int
    guests: list[Guest]


async def _view(conn: Conn, row: DictRow) -> Trip:
    guests = [_guest(g) for g in await store.guests(conn, row["id"])]
    own = {k: row[k] for k in Trip.model_fields if k not in ("ends_on", "guests")}
    return Trip(**own, ends_on=store.ends_on(row["starts_on"], row["days"]), guests=guests)


@router.post("/trips", status_code=status.HTTP_201_CREATED)
async def create_trip(body: NewTrip, workspace_id: Workspace, conn: Db) -> Trip:
    trip_id = await store.create_trip(conn, workspace_id, store.NewTrip(**body.model_dump()))
    return await _view(conn, (await store.trip(conn, workspace_id, trip_id)))


@router.get("/trips")
async def list_trips(workspace_id: Workspace, conn: Db) -> list[Trip]:
    return [await _view(conn, row) for row in await store.trips(conn, workspace_id)]


@router.get("/trips/{trip_id}")
async def get_trip(trip_id: UUID, workspace_id: Workspace, conn: Db) -> Trip:
    return await _view(conn, (await store.trip(conn, workspace_id, trip_id)))


@router.post("/trips/{trip_id}/guests", status_code=status.HTTP_201_CREATED)
async def invite_guest(trip_id: UUID, body: NewGuest, workspace_id: Workspace, conn: Db) -> Trip:
    await store.invite(conn, workspace_id, trip_id, body.name)
    return await _view(conn, (await store.trip(conn, workspace_id, trip_id)))


class Event(BaseModel):
    at: datetime
    guest: str
    purpose: str
    kind: str
    detail: dict[str, object]


@router.get("/trips/{trip_id}/timeline")
async def trip_timeline(trip_id: UUID, workspace_id: Workspace, conn: Db) -> list[Event]:
    return [Event(**row) for row in await store.timeline(conn, workspace_id, trip_id)]
