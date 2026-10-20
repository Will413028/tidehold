"""Model calls under a daily cap, with replay of recorded results (threat model surface 3).

Ported from dive-trip-agent@8065eba (`transactions.start_agent_call`, `replay_agent_call`,
`finish_agent_call`): keyed by workspace instead of booking, and by purpose, since compiling
wording and proposing an event set share one cap.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from tidehold.platform.db import Conn, insert_returning
from tidehold.platform.errors import Conflict, NotFound

Purpose = Literal["compile", "event_set", "draft_ask"]

# Serialises cap checks across requests; released when the transaction ends.
_CAP_LOCK = "tidehold.model_calls.cap"


def digest(purpose: Purpose, model: str, prompt_hash: str, payload: object) -> str:
    """Equal digests mean the same model would be asked the same thing with the same prompt."""
    text = json.dumps([purpose, model, prompt_hash, payload], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Ask:
    """One question to one model: who asks, why, and the digest of the exact input."""

    workspace_id: UUID
    purpose: Purpose
    model: str
    prompt_hash: str
    input_digest: str


@dataclass(frozen=True)
class Cap:
    daily: int
    today: date
    # a tighter daily cap for this purpose alone, so drafting asks leaves compiles their share
    purpose_daily: int | None = None


async def start(conn: Conn, ask: Ask, cap: Cap) -> DictRow:
    """A started live call while today's cap allows one; past it, a replay of the latest
    successful live call with the same digest, else a capped call. Every started live call
    counts, even one that later fails or never finishes."""
    async with conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (_CAP_LOCK,))
        used = await (
            await conn.execute(
                "select count(*) as n from model_calls where day = %s and mode = 'live'",
                (cap.today,),
            )
        ).fetchone()
        used_by_purpose = await (
            await conn.execute(
                "select count(*) as n from model_calls"
                " where day = %s and mode = 'live' and purpose = %s",
                (cap.today, ask.purpose),
            )
        ).fetchone()
        row = (ask.workspace_id, ask.purpose, cap.today, ask.input_digest, ask.model)
        purpose_room = cap.purpose_daily is None or (
            used_by_purpose is not None and used_by_purpose["n"] < cap.purpose_daily
        )
        if used is not None and used["n"] < cap.daily and purpose_room:
            return await insert_returning(
                conn,
                "insert into model_calls (workspace_id, purpose, day, mode, status,"
                " input_digest, model, prompt_hash)"
                " values (%s, %s, %s, 'live', 'started', %s, %s, %s) returning *",
                (*row, ask.prompt_hash),
            )
        return await replay(conn, ask, cap.today) or (
            await insert_returning(
                conn,
                "insert into model_calls (workspace_id, purpose, day, mode, status,"
                " input_digest, model, prompt_hash, error_code, finished_at)"
                " values (%s, %s, %s, 'capped', 'failed', %s, %s, %s, 'DAILY_CAP_REACHED',"
                " clock_timestamp()) returning *",
                (*row, ask.prompt_hash),
            )
        )


async def replay(conn: Conn, ask: Ask, today: date) -> DictRow | None:
    """Record a replay of the latest successful live result for the same digest, if any. The
    digest covers the whole input, so a replay never shows a workspace another's wording."""
    return await (
        await conn.execute(
            "insert into model_calls (workspace_id, purpose, day, mode, status, input_digest,"
            " model, prompt_hash, replay_of, result, finished_at)"
            " select %s, purpose, %s, 'recorded', 'succeeded', input_digest, model, prompt_hash,"
            " id, result, clock_timestamp() from model_calls"
            " where mode = 'live' and status = 'succeeded' and purpose = %s"
            " and input_digest = %s order by created_at desc limit 1 returning *",
            (ask.workspace_id, today, ask.purpose, ask.input_digest),
        )
    ).fetchone()


async def finish(
    conn: Conn, call_id: UUID, *, result: dict[str, Any] | None, error_code: str | None
) -> None:
    row = await (
        await conn.execute(
            "update model_calls set status = %s, result = %s, error_code = %s,"
            " finished_at = clock_timestamp() where id = %s and status = 'started' returning id",
            (
                "failed" if error_code else "succeeded",
                Jsonb(result) if result is not None else None,
                error_code[:200] if error_code else None,
                call_id,
            ),
        )
    ).fetchone()
    if row is None:
        raise Conflict("MODEL_CALL_NOT_STARTED")


async def succeeded(conn: Conn, workspace_id: UUID, call_id: UUID, purpose: Purpose) -> DictRow:
    """A successful (live or recorded) call of the workspace, or NotFound."""
    row = await (
        await conn.execute(
            "select * from model_calls where id = %s and workspace_id = %s and purpose = %s"
            " and status = 'succeeded'",
            (call_id, workspace_id, purpose),
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return row
