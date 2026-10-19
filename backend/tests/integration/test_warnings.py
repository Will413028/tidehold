"""The evidence store against PostgreSQL with recorded feeds (plan step 7 acceptance)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.identity.sessions import new_workspace
from tidehold.platform.db import Conn
from tidehold.warnings import store

FIXTURES = Path(__file__).parent.parent / "fixtures" / "warnings"
NWS_FETCHED = datetime(2026, 10, 19, 11, 0, 21, 15505, tzinfo=UTC)


async def _ingest(conn: Conn) -> dict[str, int]:
    return await store.ingest(conn, store.envelopes(sorted(FIXTURES.glob("*.json.gz"))))


async def _gale(conn: Conn) -> DictRow:
    row = await (
        await conn.execute(
            "select id, area_codes, onset, expires from official_warnings"
            " where source = 'nws' and event = 'Gale Warning' and onset < %s and expires > %s"
            " order by identifier limit 1",
            (NWS_FETCHED, NWS_FETCHED),
        )
    ).fetchone()
    assert row is not None
    return row


def _query(workspace: UUID, gale: DictRow, at: datetime) -> store.EvidenceQuery:
    return store.EvidenceQuery(
        workspace_id=workspace,
        source="nws",
        areas=gale["area_codes"][:1],
        decided_at=at,
        window=(at, at + timedelta(hours=1)),
    )


async def test_recorded_feeds_are_ingested_once(conn: Conn) -> None:
    first = await _ingest(conn)
    again = await _ingest(conn)

    assert first == {"new": 260, "seen": 0, "unreadable": 0}
    assert again == {"new": 0, "seen": 260, "unreadable": 0}


async def test_evidence_fetched_after_the_decision_time_is_invisible(conn: Conn) -> None:
    await _ingest(conn)
    workspace, _ = await new_workspace(conn)
    gale = await _gale(conn)

    before = await store.evidence(conn, _query(workspace, gale, NWS_FETCHED - timedelta(seconds=1)))
    after = await store.evidence(conn, _query(workspace, gale, NWS_FETCHED))

    assert before == []
    assert [(r["id"], r["replayed"], r["recorded_at"]) for r in after] == [
        (gale["id"], False, NWS_FETCHED)
    ]


async def test_a_replayed_warning_is_seen_only_by_its_workspace_from_its_shifted_time(
    conn: Conn,
) -> None:
    await _ingest(conn)
    demo, _ = await new_workspace(conn)
    other, _ = await new_workspace(conn)
    gale = await _gale(conn)
    lands = datetime(2026, 12, 6, 6, tzinfo=UTC)  # inside a trip on 2026-12-05..07

    await store.replay(conn, demo, gale["id"], lands)
    early = await store.evidence(conn, _query(demo, gale, lands - timedelta(minutes=1)))
    seen = await store.evidence(conn, _query(demo, gale, lands))
    elsewhere = await store.evidence(conn, _query(other, gale, lands))

    assert early == []
    assert [(r["replayed"], r["recorded_at"], r["visible_at"]) for r in seen] == [
        (True, NWS_FETCHED, lands)
    ]
    assert seen[0]["onset"] < lands < seen[0]["expires"]  # in force when it becomes visible
    assert elsewhere == []
