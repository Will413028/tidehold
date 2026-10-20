"""Operator routes for policy versions (workspace access class)."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection
from tidehold.policy import calls, service, versions
from tidehold.policy.schema import Quote, Rule

router = APIRouter(prefix="/api")

Workspace = Annotated[UUID, Depends(current_workspace)]
Db = Annotated[Conn, Depends(connection)]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, le=50)
    answer: str = Field(min_length=1, max_length=versions.MAX_ANSWER)


class NewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wording: str = Field(min_length=1, max_length=versions.MAX_WORDING)
    # The compilation the operator started from; null when it wrote the rules by hand.
    compilation_id: UUID | None = None
    rules: list[Rule] = Field(max_length=30)
    # One answer per item the compilation raised, by its index.
    rulings: list[Answer] = Field(default_factory=list, max_length=50)


class Clause(BaseModel):
    """A rule as stored, with its clause cut from the stored wording (never the model's text)."""

    rule: dict[str, Any]
    clause: str
    warning_clause: str | None


class Version(BaseModel):
    id: UUID
    created_at: datetime
    compilation_id: UUID | None
    wording: str
    warning_source: str | None
    products: list[str]
    rules: list[Clause]
    rulings: list[dict[str, Any]]


def _cut(wording: str, span: list[int] | None) -> str | None:
    return None if span is None else wording[span[0] : span[1]]


def _clauses(wording: str, rules: list[dict[str, Any]]) -> list[Clause]:
    return [
        Clause(
            rule=item["rule"],
            clause=_cut(wording, item["span"]) or "",
            warning_clause=_cut(wording, item["warning_span"]),
        )
        for item in rules
    ]


def _view(row: DictRow) -> Version:
    wording: str = row["wording"]
    return Version(
        id=row["id"],
        created_at=row["created_at"],
        compilation_id=row["compilation_id"],
        wording=wording,
        warning_source=row["warning_source"],
        products=sorted(versions.products(row)),
        rules=_clauses(wording, row["rules"]),
        rulings=row["rulings"],
    )


@router.post("/policy-versions", status_code=status.HTTP_201_CREATED)
async def publish(body: NewVersion, workspace_id: Workspace, conn: Db) -> Version:
    asked = []
    if body.compilation_id is not None:
        call = await service.compilation_for(conn, workspace_id, body.compilation_id, body.wording)
        asked = service.asked(call["result"])
    answers = [versions.Answer(a.index, a.answer) for a in body.rulings]
    checked = versions.check(body.wording, body.rules, asked, answers)
    version_id = await versions.publish(
        conn, workspace_id, body.wording, checked, body.compilation_id
    )
    return _view(await versions.version(conn, workspace_id, version_id))


@router.get("/policy-versions")
async def list_versions(workspace_id: Workspace, conn: Db) -> list[Version]:
    return [_view(row) for row in await versions.versions(conn, workspace_id)]


@router.get("/policy-versions/{version_id}")
async def get_version(version_id: UUID, workspace_id: Workspace, conn: Db) -> Version:
    return _view(await versions.version(conn, workspace_id, version_id))


def _context(request: Request, workspace_id: UUID, conn: Conn) -> service.Context:
    daily: int = request.app.state.settings.model_daily_live_cap
    cap = calls.Cap(daily=daily, today=datetime.now(UTC).date())
    return service.Context(conn, workspace_id, request.app.state.compiler, cap)


class NewCompilation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wording: str = Field(min_length=1, max_length=versions.MAX_WORDING)


class Asked(BaseModel):
    index: int
    topic: str
    question: str
    quote: str | None
    clause: str | None


class Compilation(BaseModel):
    """What the model proposed, for the operator to confirm, edit or replace. Without a model
    answer (`mode` "no_ai", with the `reason`), the operator writes rules by hand."""

    id: UUID | None
    mode: service.Mode
    reason: service.Reason | None
    model: str | None
    prompt_hash: str | None
    wording: str
    rules: list[Clause]
    needs_ruling: list[Asked]


def _compilation(wording: str, outcome: service.Outcome) -> Compilation:
    call, result = outcome.call, outcome.result
    if call is None or result is None:
        return Compilation(
            id=None,
            mode=outcome.mode,
            reason=outcome.reason,
            model=None,
            prompt_hash=None,
            wording=wording,
            rules=[],
            needs_ruling=[],
        )
    return Compilation(
        id=call["id"],
        mode=outcome.mode,
        reason=None,
        model=call["model"],
        prompt_hash=call["prompt_hash"],
        wording=wording,
        rules=_clauses(wording, result["rules"]),
        needs_ruling=[
            Asked(
                index=index,
                topic=item["ruling"]["topic"],
                question=item["ruling"]["question"],
                quote=item["ruling"]["quote"],
                clause=_cut(wording, item["span"]),
            )
            for index, item in enumerate(result["needs_ruling"])
        ],
    )


@router.post("/policy-compilations", status_code=status.HTTP_201_CREATED)
async def compile_wording(
    body: NewCompilation, request: Request, workspace_id: Workspace, conn: Db
) -> Compilation:
    outcome = await service.compile_wording(_context(request, workspace_id, conn), body.wording)
    return _compilation(body.wording, outcome)


@router.get("/policy-compilations/{compilation_id}")
async def get_compilation(compilation_id: UUID, workspace_id: Workspace, conn: Db) -> Compilation:
    call = await calls.succeeded(conn, workspace_id, compilation_id, "compile")
    mode: service.Mode = "recorded" if call["mode"] == "recorded" else "live"
    return _compilation(call["result"]["wording"], service.Outcome(mode, None, call))


class NewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=20)
    # The warning condition's clause, as the compilation or the operator quoted it.
    quote: Quote


class Proposal(BaseModel):
    """Event values the model proposes for the operator to confirm; with no model answer,
    `values` is empty and the operator picks from `catalogue` by hand."""

    mode: service.Mode
    reason: service.Reason | None
    values: list[str]
    catalogue: list[list[str]]


@router.post("/event-set-proposals", status_code=status.HTTP_201_CREATED)
async def propose_event_set(
    body: NewProposal, request: Request, workspace_id: Workspace, conn: Db
) -> Proposal:
    context = _context(request, workspace_id, conn)
    outcome = await service.propose(context, body.source, body.quote)
    values: list[str] = outcome.result["values"] if outcome.result else []
    return Proposal(
        mode=outcome.mode,
        reason=outcome.reason,
        values=values,
        catalogue=service.catalogue_of(body.source),
    )
