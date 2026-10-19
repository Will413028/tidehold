"""The evidence store: recorded official warnings, their replays, and what a decision may see.

Settlement asks for evidence as of a decision time; only warnings fetched (or, when replayed,
shifted to) at or before that time are returned (invariant: a decision sees only evidence
fetched before the decision time). Of those, only Actual messages that no visible Update or
Cancel has replaced count; a Cancel itself is never evidence.
"""

import gzip
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from tidehold.platform.db import Conn
from tidehold.platform.errors import NotFound
from tidehold.warnings.cap import OfficialWarning, Source, UnreadableWarning, from_envelope


async def record(conn: Conn, warning: OfficialWarning, fetched_at: datetime) -> bool:
    """Store a warning once, keeping its earliest fetch time; True when it was new. The
    lifecycle fields are rewritten on conflict so rows recorded before they existed fill in."""
    row = await (
        await conn.execute(
            "insert into official_warnings (source, identifier, sent, onset, expires, event,"
            " severity, msg_type, headline, area_desc, area_codes, parameters, url, fetched_at,"
            " status, supersedes, event_codes)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict (source, identifier) do update"
            "   set fetched_at = least(official_warnings.fetched_at, excluded.fetched_at),"
            "       status = excluded.status, supersedes = excluded.supersedes,"
            "       event_codes = excluded.event_codes"
            " returning (xmax = 0) as inserted",
            (
                warning.source,
                warning.identifier,
                warning.sent,
                warning.onset,
                warning.expires,
                warning.event,
                warning.severity,
                warning.msg_type,
                warning.headline,
                warning.area_desc,
                list(warning.area_codes),
                Jsonb(warning.parameters),
                warning.url,
                fetched_at,
                warning.status,
                list(warning.supersedes),
                Jsonb(warning.event_codes),
            ),
        )
    ).fetchone()
    return bool(row and row["inserted"])


def envelopes(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        envelope: dict[str, Any] = json.loads(gzip.decompress(path.read_bytes()))
        yield envelope


async def ingest(conn: Conn, recorded: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Read recorder envelopes into the store. Re-running is harmless (one row per warning,
    earliest fetch kept); an unreadable document is counted, not fatal."""
    counts = {"new": 0, "seen": 0, "unreadable": 0}
    for envelope in recorded:
        try:
            warnings = from_envelope(envelope)
        except UnreadableWarning:
            counts["unreadable"] += 1
            continue
        fetched_at = datetime.fromisoformat(str(envelope["fetched_at"]))
        for warning in warnings:
            counts["new" if await record(conn, warning, fetched_at) else "seen"] += 1
    return counts


async def replay(conn: Conn, workspace_id: UUID, warning_id: UUID, fetched_at: datetime) -> UUID:
    """Replay a recorded warning into a workspace as if it had been fetched at `fetched_at`
    (D12). Aligning the fetch time, not the onset, keeps the replayed warning in force at the
    moment it becomes visible, as it was when recorded."""
    warning = await (
        await conn.execute("select fetched_at from official_warnings where id = %s", (warning_id,))
    ).fetchone()
    if warning is None:
        raise NotFound
    shift: timedelta = fetched_at - warning["fetched_at"]
    row = await (
        await conn.execute(
            "insert into warning_replays (workspace_id, warning_id, shift) values (%s, %s, %s)"
            " on conflict (workspace_id, warning_id) do update set shift = excluded.shift"
            " returning id",
            (workspace_id, warning_id, shift),
        )
    ).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING returned no row")
    replay_id: UUID = row["id"]
    return replay_id


# A replacing message only replaces within its own timeline: live by live, and replayed by
# replayed in the same workspace, so a live Update fetched long ago cannot hide a replay.
EVIDENCE = """
with evidence as (
    select w.*, null::interval as shift, w.fetched_at as visible_at, false as replayed
    from official_warnings w
    union all
    select w.*, r.shift, w.fetched_at + r.shift as visible_at, true as replayed
    from official_warnings w join warning_replays r on r.warning_id = w.id
    where r.workspace_id = %(workspace)s
),
visible as (
    select * from evidence where source = %(source)s and visible_at <= %(at)s
)
select id, source, identifier, event, severity, msg_type, status, event_codes, headline,
       area_desc, area_codes, parameters, url, replayed,
       fetched_at as recorded_at,
       visible_at,
       onset + coalesce(shift, interval '0') as onset,
       expires + coalesce(shift, interval '0') as expires
from visible v
where area_codes && %(areas)s
  and status = 'Actual' and msg_type <> 'Cancel'
  and not exists (
      select 1 from visible later
      where later.replayed = v.replayed and v.identifier = any(later.supersedes)
  )
  and onset + coalesce(shift, interval '0') < %(until)s
  and expires + coalesce(shift, interval '0') > %(since)s
order by visible_at, id
"""


@dataclass(frozen=True)
class EvidenceQuery:
    """Warnings from `source` for any of `areas`, in force during `window`, as visible at
    `decided_at` to `workspace_id`: live ones by fetch time, replayed ones by shifted time."""

    workspace_id: UUID
    source: Source
    areas: list[str]
    decided_at: datetime
    window: tuple[datetime, datetime]


async def evidence(conn: Conn, query: EvidenceQuery) -> list[DictRow]:
    since, until = query.window
    return await (
        await conn.execute(
            EVIDENCE,
            {
                "workspace": query.workspace_id,
                "source": query.source,
                "areas": query.areas,
                "at": query.decided_at,
                "since": since,
                "until": until,
            },
        )
    ).fetchall()
