import secrets
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection, insert_returning
from tidehold.platform.errors import NotFound

router = APIRouter(prefix="/api")
guest_router = APIRouter(prefix="/api")

# Longer trips cannot settle inside one PayPal authorization (deposit/balance ADR).
MAX_TRIP_DAYS = 10


class NewTrip(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    starts_on: date
    days: int = Field(ge=1, le=MAX_TRIP_DAYS)


class Trip(BaseModel):
    id: UUID
    name: str
    starts_on: date
    ends_on: date
    days: int
    guest_token: str


class GuestTrip(BaseModel):
    """What a guest link shows: no workspace or internal ids."""

    name: str
    starts_on: date
    ends_on: date
    days: int


def _ends_on(starts_on: date, days: int) -> date:
    return starts_on + timedelta(days=days - 1)


@router.post("/trips", status_code=status.HTTP_201_CREATED)
async def create_trip(
    body: NewTrip,
    workspace_id: Annotated[UUID, Depends(current_workspace)],
    conn: Annotated[Conn, Depends(connection)],
) -> Trip:
    guest_token = secrets.token_urlsafe(24)
    row = await insert_returning(
        conn,
        "insert into trips (workspace_id, name, starts_on, days, guest_token)"
        " values (%s, %s, %s, %s, %s) returning id",
        (workspace_id, body.name, body.starts_on, body.days, guest_token),
    )
    return Trip(
        id=row["id"],
        name=body.name,
        starts_on=body.starts_on,
        ends_on=_ends_on(body.starts_on, body.days),
        days=body.days,
        guest_token=guest_token,
    )


@router.get("/trips")
async def list_trips(
    workspace_id: Annotated[UUID, Depends(current_workspace)],
    conn: Annotated[Conn, Depends(connection)],
) -> list[Trip]:
    rows = await (
        await conn.execute(
            "select id, name, starts_on, days, guest_token from trips"
            " where workspace_id = %s order by starts_on, created_at",
            (workspace_id,),
        )
    ).fetchall()
    return [Trip(**row, ends_on=_ends_on(row["starts_on"], row["days"])) for row in rows]


@router.get("/trips/{trip_id}")
async def get_trip(
    trip_id: UUID,
    workspace_id: Annotated[UUID, Depends(current_workspace)],
    conn: Annotated[Conn, Depends(connection)],
) -> Trip:
    row = await (
        await conn.execute(
            "select id, name, starts_on, days, guest_token from trips"
            " where id = %s and workspace_id = %s",
            (trip_id, workspace_id),
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return Trip(**row, ends_on=_ends_on(row["starts_on"], row["days"]))


@guest_router.get("/guest/{guest_token}")
async def guest_trip(guest_token: str, conn: Annotated[Conn, Depends(connection)]) -> GuestTrip:
    row = await (
        await conn.execute(
            "select name, starts_on, days from trips where guest_token = %s", (guest_token,)
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return GuestTrip(**row, ends_on=_ends_on(row["starts_on"], row["days"]))
