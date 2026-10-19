"""Operator routes for policy versions (workspace access class)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from psycopg.rows import DictRow
from pydantic import BaseModel, ConfigDict, Field

from tidehold.identity.sessions import current_workspace
from tidehold.platform.db import Conn, connection
from tidehold.policy import versions
from tidehold.policy.schema import Rule

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
    rules: list[Rule] = Field(max_length=30)
    rulings: list[Answer] = Field(default_factory=list, max_length=50)


class Clause(BaseModel):
    """A rule as stored, with its clause cut from the stored wording (never the model's text)."""

    rule: dict[str, Any]
    clause: str
    warning_clause: str | None


class Version(BaseModel):
    id: UUID
    created_at: datetime
    wording: str
    warning_source: str | None
    products: list[str]
    rules: list[Clause]
    rulings: list[dict[str, Any]]


def _cut(wording: str, span: list[int] | None) -> str | None:
    return None if span is None else wording[span[0] : span[1]]


def _view(row: DictRow) -> Version:
    wording: str = row["wording"]
    return Version(
        id=row["id"],
        created_at=row["created_at"],
        wording=wording,
        warning_source=row["warning_source"],
        products=sorted(versions.products(row)),
        rules=[
            Clause(
                rule=item["rule"],
                clause=_cut(wording, item["span"]) or "",
                warning_clause=_cut(wording, item["warning_span"]),
            )
            for item in row["rules"]
        ],
        rulings=row["rulings"],
    )


@router.post("/policy-versions", status_code=status.HTTP_201_CREATED)
async def publish(body: NewVersion, workspace_id: Workspace, conn: Db) -> Version:
    answers = [versions.Answer(a.index, a.answer) for a in body.rulings]
    checked = versions.check(body.wording, body.rules, [], answers)
    version_id = await versions.publish(conn, workspace_id, body.wording, checked)
    return _view(await versions.version(conn, workspace_id, version_id))


@router.get("/policy-versions")
async def list_versions(workspace_id: Workspace, conn: Db) -> list[Version]:
    return [_view(row) for row in await versions.versions(conn, workspace_id)]


@router.get("/policy-versions/{version_id}")
async def get_version(version_id: UUID, workspace_id: Workspace, conn: Db) -> Version:
    return _view(await versions.version(conn, workspace_id, version_id))
