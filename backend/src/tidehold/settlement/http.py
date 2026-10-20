"""Operator routes for cancellations and proposals (workspace access class, plan step 8a)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection
from tidehold.platform.errors import Unavailable
from tidehold.settlement import matching
from tidehold.settlement import proposals as store
from tidehold.trips import store as trips
from tidehold.warnings import openmeteo

router = APIRouter(prefix="/api")

Workspace = Annotated[UUID, Depends(current_workspace)]
Db = Annotated[Conn, Depends(connection)]
Index = Annotated[int, Field(strict=True, ge=0, le=20)]


class NewCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # a trip day, or the rest of the trip from that day (the whole trip from day 1)
    scope: Literal["day", "rest"]
    trip_day: int = Field(strict=True, ge=1, le=10)
    cause: Literal["weather", "safety", "other"]


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deposit: Index
    balance: Index


class Decision(BaseModel):
    """Exactly one of `accept` (a settlement's option per purpose), `reject` (a settlement)
    or `dismiss` (any other ask)."""

    model_config = ConfigDict(extra="forbid")

    accept: Choice | None = None
    reject: Literal[True] | None = None
    dismiss: Literal[True] | None = None

    @model_validator(mode="after")
    def _one(self) -> "Decision":
        if sum(v is not None for v in (self.accept, self.reject, self.dismiss)) != 1:
            raise ValueError("exactly one of accept, reject, dismiss")
        return self


class Proposal(BaseModel):
    """What the operator decides on: the stored facts and template question are shown beside
    the model's draft, so a decision never rests on drafted text alone."""

    id: UUID
    kind: str
    subject: str
    state: str
    cancellation_id: UUID | None
    reasons: list[str]
    parts: dict[str, Any]
    choice: dict[str, int] | None
    decided_by: str | None
    question: str
    draft: str | None
    facts: dict[str, Any]
    created_at: datetime
    decided_at: datetime | None


def _view(row: DictRow) -> Proposal:
    return Proposal(**{k: row[k] for k in Proposal.model_fields})


@router.post("/trips/{trip_id}/cancellations", status_code=status.HTTP_201_CREATED)
async def record_cancellation(
    trip_id: UUID, body: NewCancellation, workspace_id: Workspace, conn: Db
) -> Proposal:
    proposal_id = await store.record_cancellation(
        conn,
        workspace_id,
        trip_id,
        store.NewCancellation(body.scope, body.trip_day, body.cause),
        datetime.now(UTC),
    )
    return _view(await store.proposal(conn, store.Ref(workspace_id, trip_id, proposal_id)))


@router.get("/trips/{trip_id}/proposals")
async def list_proposals(trip_id: UUID, workspace_id: Workspace, conn: Db) -> list[Proposal]:
    return [_view(row) for row in await store.proposals(conn, workspace_id, trip_id)]


@router.post("/trips/{trip_id}/proposals/{proposal_id}/decision")
async def decide(
    trip_id: UUID, proposal_id: UUID, body: Decision, workspace_id: Workspace, conn: Db
) -> Proposal:
    ref = store.Ref(workspace_id, trip_id, proposal_id)
    decision = store.OperatorDecision(
        accept=body.accept.model_dump() if body.accept else None,
        reject=body.reject is True,
        dismiss=body.dismiss is True,
    )
    await store.decide_proposal(conn, ref, decision, datetime.now(UTC))
    return _view(await store.proposal(conn, ref))


class Warning(BaseModel):
    """A warning in force on the trip's days, as visible now: `replayed` ones were recorded at
    `recorded_at` and replayed (D12)."""

    source: str
    identifier: str
    event: str
    value: str
    area_desc: str
    onset: datetime
    expires: datetime
    recorded_at: datetime
    visible_at: datetime
    replayed: bool
    url: str
    trip_days: list[int]


class ForecastDay(BaseModel):
    day: date
    max_wave_height_m: Decimal | None


class Forecast(BaseModel):
    """Corroboration only: a forecast never settles or conditions anything (D16)."""

    status: Literal["ok", "unavailable"]
    attribution: str
    fetched_at: datetime | None
    url: str | None
    days: list[ForecastDay]


class Evidence(BaseModel):
    warnings: list[Warning]
    forecast: Forecast


def _warnings(rows: list[Any]) -> list[Warning]:
    """One entry per warning, with every trip day it was found in force on."""
    by_id: dict[Any, Warning] = {}
    for row in rows:
        seen = by_id.get(row["id"])
        if seen is None:
            summary = matching.summary(row)
            del summary["trip_day"]
            by_id[row["id"]] = Warning(**summary, trip_days=[row["trip_day"]])
        elif row["trip_day"] not in seen.trip_days:
            seen.trip_days.append(row["trip_day"])
    return list(by_id.values())


async def _forecast(request: Request, conn: Conn, trip: Any, now: datetime) -> Forecast:
    site = openmeteo.Site(trip["site_lat"], trip["site_lon"], trip["timezone"])
    days = (trip["starts_on"], trips.ends_on(trip["starts_on"], trip["days"]))
    try:
        rows = await openmeteo.forecast(conn, request.app.state.forecasts, site, days, now)
    except Unavailable:
        return Forecast(
            status="unavailable",
            attribution=openmeteo.ATTRIBUTION,
            fetched_at=None,
            url=None,
            days=[],
        )
    return Forecast(
        status="ok",
        attribution=openmeteo.ATTRIBUTION,
        fetched_at=rows[0].fetched_at if rows else None,
        url=rows[0].url if rows else None,
        days=[ForecastDay(day=r.day, max_wave_height_m=r.max_wave_height_m) for r in rows],
    )


@router.get("/trips/{trip_id}/evidence")
async def trip_evidence(
    trip_id: UUID, request: Request, workspace_id: Workspace, conn: Db
) -> Evidence:
    """What the operator decides a cancellation from: the warnings visible now in force on any
    local trip day, and the site's marine forecast."""
    now = datetime.now(UTC)
    trip = await trips.trip(conn, workspace_id, trip_id)
    rows = await matching.in_force(conn, trip, range(1, trip["days"] + 1), now)
    return Evidence(warnings=_warnings(rows), forecast=await _forecast(request, conn, trip, now))
