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
from tidehold.platform.errors import Conflict, Invalid, Unavailable
from tidehold.settlement import guest, matching, watch
from tidehold.settlement import proposals as store
from tidehold.trips import store as trips
from tidehold.warnings import openmeteo
from tidehold.warnings import store as warnings
from tidehold.warnings.cap import Source

router = APIRouter(prefix="/api")
# guest access class: the unguessable token is the only credential
guest_router = APIRouter(prefix="/api/guest/{token}")

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
    """One entry per warning and timeline (a live copy and its replay are both shown), with
    every trip day it was found in force on."""
    by_id: dict[tuple[Any, bool], Warning] = {}
    for row in rows:
        key = (row["id"], row["replayed"])
        seen = by_id.get(key)
        if seen is None:
            summary = matching.summary(row)
            del summary["trip_day"]
            by_id[key] = Warning(**summary, trip_days=[row["trip_day"]])
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


async def _evidence(request: Request, conn: Conn, trip: Any, now: datetime) -> Evidence:
    rows = await matching.in_force(conn, trip, range(1, trip["days"] + 1), now)
    return Evidence(warnings=_warnings(rows), forecast=await _forecast(request, conn, trip, now))


@router.get("/trips/{trip_id}/evidence")
async def trip_evidence(
    trip_id: UUID, request: Request, workspace_id: Workspace, conn: Db
) -> Evidence:
    """What the operator decides a cancellation from: the warnings visible now in force on any
    local trip day, and the site's marine forecast."""
    trip = await trips.trip(conn, workspace_id, trip_id)
    return await _evidence(request, conn, trip, datetime.now(UTC))


class Recorded(BaseModel):
    id: UUID
    source: str
    identifier: str
    event: str
    headline: str | None
    area_desc: str
    area_codes: list[str]
    onset: datetime
    expires: datetime
    recorded_at: datetime


AreaCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,24}$")]


@router.get("/recorded-warnings")
async def recorded_warnings(
    source: Source, conn: Db, area: AreaCode | None = None
) -> list[Recorded]:
    """The recorded warnings an operator can replay into a demo (D12), newest first; recorded
    feeds are public, and the router's session dependency still requires a workspace."""
    return [Recorded(**row) for row in await warnings.recorded(conn, source, area)]


class NewReplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warning_id: UUID


@router.post("/trips/{trip_id}/replays", status_code=status.HTTP_201_CREATED)
async def replay_warning(
    trip_id: UUID, body: NewReplay, request: Request, workspace_id: Workspace, conn: Db
) -> Evidence:
    """Replay a recorded warning into the workspace as fetched now (D12) and check the trip at
    once, so the asks it calls for exist when this answers."""
    now = datetime.now(UTC)
    trip = await trips.trip(conn, workspace_id, trip_id)
    if trip["cancelled_at"] is not None:
        raise Conflict("TRIP_CANCELLED")
    warning = await warnings.warning(conn, body.warning_id)
    if warning["source"] != trip["warning_source"]:
        raise Invalid("WARNING_SOURCE_MISMATCH")
    async with conn.transaction():
        await warnings.replay(conn, workspace_id, body.warning_id, now)
        await watch.check(conn, trip_id, now)
    return await _evidence(request, conn, await trips.trip(conn, workspace_id, trip_id), now)


class FailedAction(BaseModel):
    """Work that gave up after its retries, one row per kind and subject."""

    kind: str
    subject: UUID
    failures: int
    last_error: str | None  # an exception class name or a RetryLater code, never a message
    finished_at: datetime


# Trip actions name the trip; payment actions a guest's current deposit or balance; proposal
# actions one of the trip's proposals.
FAILED = """
select kind, coalesce(trip_id, payment_id, proposal_id) as subject, count(*) as failures,
       (array_agg(last_error order by finished_at desc))[1] as last_error,
       max(finished_at) as finished_at
from due_actions
where state = 'failed' and (
    trip_id = %(trip)s
    or payment_id in (
        select unnest(array[deposit_payment_id, balance_payment_id])
        from guests where trip_id = %(trip)s)
    or proposal_id in (select id from proposals where trip_id = %(trip)s))
group by kind, subject
order by finished_at desc
"""


@router.get("/trips/{trip_id}/failed-actions")
async def failed_actions(trip_id: UUID, workspace_id: Workspace, conn: Db) -> list[FailedAction]:
    """Failed work stays for the operator: `request_balance` is never recreated after a
    failure, and the recreated kinds would otherwise add a row per failure."""
    await trips.trip(conn, workspace_id, trip_id)
    rows = await (await conn.execute(FAILED, {"trip": trip_id})).fetchall()
    return [FailedAction(**row) for row in rows]


class GuestWarning(BaseModel):
    source: str
    event: str
    area_desc: str
    onset: datetime
    expires: datetime
    recorded_at: datetime
    replayed: bool
    url: str


class GuestOutcome(BaseModel):
    purpose: str
    kind: str
    amount_minor: int
    credit_expiry_months: int | None
    # the clauses behind the outcome, cut from the wording when the operator decided
    clauses: list[str]


class GuestSettlement(BaseModel):
    """One cancellation as the guest sees it; `asked` means the operator is deciding."""

    state: str
    days: list[int]
    whole_trip: bool
    cause: str
    recorded_at: datetime
    warnings: list[GuestWarning]
    outcomes: list[GuestOutcome]
    explanation: str


class GuestCredit(BaseModel):
    purpose: str
    amount_minor: int
    expires_at: datetime | None


class GuestSettlements(BaseModel):
    """Only this guest's lines: no other guest's name or amount, and no internal ids."""

    settlements: list[GuestSettlement]
    credits: list[GuestCredit]


@guest_router.get("/settlement")
async def guest_settlement(token: str, conn: Db) -> GuestSettlements:
    settlements, credits = await guest.view(conn, token)
    return GuestSettlements(
        settlements=[
            GuestSettlement(
                state=s.state,
                days=s.days,
                whole_trip=s.whole_trip,
                cause=s.cause,
                recorded_at=s.recorded_at,
                warnings=[GuestWarning(**w) for w in s.warnings],
                outcomes=[GuestOutcome(**vars(o)) for o in s.outcomes],
                explanation=s.explanation,
            )
            for s in settlements
        ],
        credits=[GuestCredit(**c) for c in credits],
    )
