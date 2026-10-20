"""Operator routes for cancellations and proposals (workspace access class, plan step 8a)."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection
from tidehold.settlement import proposals as store

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
