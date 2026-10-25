"""The evidence store against PostgreSQL with recorded feeds (plan step 7 acceptance)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.identity.sessions import new_workspace
from tidehold.platform.db import Conn
from tidehold.settlement import matching
from tidehold.warnings import store
from tidehold.warnings.cap import parse_cap

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

    assert first == {"new": 261, "seen": 0, "unreadable": 0}
    assert again == {"new": 0, "seen": 261, "unreadable": 0}


async def test_a_stored_pagasa_typhoon_warning_matches_on_its_signal(conn: Conn) -> None:
    await _ingest(conn)
    row = await (
        await conn.execute(
            "select * from official_warnings where source = 'pagasa'"
            " and event = 'Tropical Cyclone Warning'"
        )
    ).fetchone()

    assert row is not None
    assert row["area_codes"] == ["020900000"]  # Batanes
    assert matching.value(row) == "Tropical Cyclone Warning: Signal #2"


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


CAP_DOC = """<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{identifier}</identifier><sender>test</sender>
  <sent>2026-10-17T00:00:00+00:00</sent><status>{status}</status><msgType>{msg_type}</msgType>
  {references}<scope>Public</scope>
  <info><language>en-GB</language><event>Orange wind warning</event><severity>Severe</severity>
    <onset>2026-10-17T00:00:00+00:00</onset><expires>2026-10-18T00:00:00+00:00</expires>
    <area><areaDesc>Middle Dalmatia</areaDesc>
      <geocode><valueName>EMMA_ID</valueName><value>HR805</value></geocode></area></info>
</alert>"""
RECORDED = datetime(2026, 10, 17, 1, tzinfo=UTC)
IN_FORCE = datetime(2026, 10, 17, 6, tzinfo=UTC)


async def _record(conn: Conn, identifier: str, fetched_at: datetime, **cap: str) -> None:
    """Record a MeteoAlarm-shaped CAP message; `cap` overrides msg_type, status, replaces."""
    fields = {"msg_type": "Alert", "status": "Actual", "replaces": ""} | cap
    replaces = fields.pop("replaces")
    references = f"<references>test,{replaces},2026-10-17T00:00:00+00:00</references>"
    body = CAP_DOC.format(
        identifier=identifier, references=references if replaces else "", **fields
    )
    [warning] = parse_cap(body, "meteoalarm", f"https://feeds.meteoalarm.org/{identifier}")
    await store.record(conn, warning, fetched_at)


async def _seen(conn: Conn, workspace: UUID, at: datetime) -> list[str]:
    query = store.EvidenceQuery(
        workspace_id=workspace,
        source="meteoalarm",
        areas=["HR805"],
        decided_at=at,
        window=(IN_FORCE, IN_FORCE + timedelta(hours=1)),
    )
    return [r["identifier"] for r in await store.evidence(conn, query)]


async def test_a_visible_cancel_withdraws_the_warning_and_is_not_evidence_itself(
    conn: Conn,
) -> None:
    workspace, _ = await new_workspace(conn)
    await _record(conn, "w1", RECORDED)
    await _record(
        conn, "w1-cancel", RECORDED + timedelta(hours=2), msg_type="Cancel", replaces="w1"
    )

    assert await _seen(conn, workspace, RECORDED + timedelta(hours=1)) == ["w1"]
    assert await _seen(conn, workspace, RECORDED + timedelta(hours=2)) == []


async def test_an_update_replaces_the_message_it_references(conn: Conn) -> None:
    workspace, _ = await new_workspace(conn)
    await _record(conn, "w1", RECORDED)
    await _record(
        conn, "w1-update", RECORDED + timedelta(hours=2), msg_type="Update", replaces="w1"
    )

    assert await _seen(conn, workspace, RECORDED + timedelta(hours=3)) == ["w1-update"]


async def test_exercise_and_test_messages_are_never_evidence(conn: Conn) -> None:
    workspace, _ = await new_workspace(conn)
    await _record(conn, "drill", RECORDED, status="Exercise")
    await _record(conn, "probe", RECORDED, status="Test")

    assert await _seen(conn, workspace, IN_FORCE) == []


async def test_a_live_update_does_not_hide_a_replay_of_the_message_it_replaced(
    conn: Conn,
) -> None:
    demo, _ = await new_workspace(conn)
    await _record(conn, "w1", RECORDED)
    await _record(
        conn, "w1-update", RECORDED + timedelta(hours=2), msg_type="Update", replaces="w1"
    )
    [original] = await (
        await conn.execute("select id from official_warnings where identifier = 'w1'")
    ).fetchall()
    lands = datetime(2026, 12, 6, 6, tzinfo=UTC)

    await store.replay(conn, demo, original["id"], lands)
    query = store.EvidenceQuery(
        workspace_id=demo,
        source="meteoalarm",
        areas=["HR805"],
        decided_at=lands,
        window=(lands, lands + timedelta(hours=1)),
    )

    assert [(r["identifier"], r["replayed"]) for r in await store.evidence(conn, query)] == [
        ("w1", True)
    ]
