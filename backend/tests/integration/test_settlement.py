"""Settlement from the operator's cancellation, asks and the deposit (plan step 8a acceptance).

No settlement test touches a model provider: the compiler of this module's app raises on any
call, and evaluation is asserted to have made none.
"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import pytest
from httpx import AsyncClient
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import TRIP, post_trip
from tidehold.bootstrap import worker
from tidehold.money import commands, service
from tidehold.platform import due_actions as due
from tidehold.platform.db import Conn, connect
from tidehold.platform.errors import Conflict
from tidehold.policy.providers import Completion, ProviderError
from tidehold.settlement import drafts, execute
from tidehold.warnings import store
from tidehold.warnings.cap import OfficialWarning

ZONE = "America/Los_Angeles"
AREA = "PZZ530"
GALE_CLAUSE = (
    "If we cancel a dive day because a Gale Warning or stronger is in force, "
    "you get a full refund for that day."
)
WORDING = (
    "Weather. " + GALE_CLAUSE + " If we cancel the whole trip for weather, you get a full "
    "refund of the trip."
)


class RaisingModel:
    """Installed as this module's compiler: settlement must never call a model."""

    model = "raising-model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        del system, user, schema, name
        self.calls += 1
        raise AssertionError("settlement called a model provider")


@pytest.fixture
def model() -> RaisingModel:
    return RaisingModel()


def _rule(**overrides: Any) -> dict[str, Any]:
    return {
        "product": None,
        "causes": ["weather"],
        "warning": None,
        "scope": "per_day",
        "applies_to": "both",
        "remedies": [
            {"kind": "refund", "percent": 100, "deductions": [], "credit_expiry_months": None}
        ],
        "chooser": None,
        "quote": GALE_CLAUSE,
        **overrides,
    }


REFUND_ALL = {"kind": "refund", "percent": 100, "deductions": [], "credit_expiry_months": None}
GALE_RULE = _rule(
    warning={
        "named_as": "Gale Warning",
        "source": "nws",
        "event_values": ["Gale Warning", "Storm Warning"],
        "quote": "a Gale Warning or stronger is in force",
    }
)
WHOLE_TRIP_RULE = _rule(
    scope="per_trip",
    quote="If we cancel the whole trip for weather, you get a full refund of the trip.",
)


def _today() -> date:
    return datetime.now(ZoneInfo(ZONE)).date()


def _now() -> datetime:
    return datetime.now(UTC)


async def _tick(conn: Conn, paypal: FakePayPal, now: datetime | None = None) -> None:
    await worker.tick(conn, worker.handlers(paypal.client()), now or _now())


async def _trip(
    operator: AsyncClient,
    rules: list[dict[str, Any]],
    rulings: list[dict[str, Any]] | None = None,
    **trip: Any,
) -> dict[str, Any]:
    """A trip starting tomorrow (local), bound to a policy of `rules`."""
    policy: dict[str, Any] = {"wording": WORDING, "rules": rules}
    if rulings is not None:
        policy["needs_ruling"] = rulings
    version = await operator.post("/api/policy-versions", json=policy)
    assert version.status_code == 201, version.text
    body = {
        **TRIP,
        "starts_on": (_today() + timedelta(days=1)).isoformat(),
        "warning_source": "nws",
        "warning_areas": [AREA],
        "timezone": ZONE,
        "policy_version_id": version.json()["id"],
        **trip,
    }
    created = await post_trip(operator, body)
    assert created.status_code == 201, created.text
    trip_row: dict[str, Any] = created.json()
    return trip_row


async def _deposit_only(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, trip_id: str
) -> str:
    """A guest who paid the deposit (sandbox test card); the balance is still unpaid."""
    invited = await operator.post(f"/api/trips/{trip_id}/guests", json={"name": "Ana"})
    token: str = invited.json()["guests"][-1]["token"]
    assert (await client.post(f"/api/guest/{token}/deposit/test-card")).status_code == 200
    await _tick(conn, paypal)
    return token


async def _guest(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, trip_id: str
) -> str:
    """A guest who paid the deposit and the balance (sandbox test card)."""
    token = await _deposit_only(operator, client, conn, paypal, trip_id)
    paid = await client.post(f"/api/guest/{token}/balance/test-card")
    assert paid.json()["balance"]["payment"]["state"] == "captured", paid.text
    return token


async def _cancel(operator: AsyncClient, trip_id: str, scope: str, day: int) -> dict[str, Any]:
    response = await operator.post(
        f"/api/trips/{trip_id}/cancellations",
        json={"scope": scope, "trip_day": day, "cause": "weather"},
    )
    assert response.status_code == 201, response.text
    proposal: dict[str, Any] = response.json()
    return proposal


async def _workspace(conn: Conn, trip_id: str) -> UUID:
    row = await (
        await conn.execute("select workspace_id from trips where id = %s", (trip_id,))
    ).fetchone()
    assert row is not None
    workspace: UUID = row["workspace_id"]
    return workspace


GALE = OfficialWarning(
    source="nws",
    identifier="urn:oid:2.49.0.1.840.0.gale-1",
    sent=datetime(2026, 1, 1, tzinfo=UTC),
    onset=datetime(2026, 1, 1, tzinfo=UTC),
    expires=datetime(2026, 1, 6, tzinfo=UTC),
    event="Gale Warning",
    severity="Moderate",
    msg_type="Alert",
    status="Actual",
    headline="Gale Warning issued",
    area_desc="Coastal waters",
    area_codes=(AREA,),
    url="https://api.weather.gov/alerts/gale-1",
)


async def _replayed(
    conn: Conn, trip_id: str, warning: OfficialWarning = GALE, *, lands: datetime | None = None
) -> None:
    """Record `warning` as fetched long ago and replay it into the trip's workspace so it is
    visible from a minute ago and in force for the next five days (D12)."""
    await store.record(conn, warning, warning.sent)
    row = await (
        await conn.execute(
            "select id from official_warnings where identifier = %s", (warning.identifier,)
        )
    ).fetchone()
    assert row is not None
    when = lands or _now() - timedelta(minutes=1)
    await store.replay(conn, await _workspace(conn, trip_id), row["id"], when)


def _moved(paypal: FakePayPal, kind: str) -> int:
    """Distinct commands of `kind` PayPal accepted (a refused call stores no answer)."""
    return sum(key.startswith(f"tidehold-{kind}-") for key in paypal.answers)


async def _proposals(operator: AsyncClient, trip_id: str) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = (await operator.get(f"/api/trips/{trip_id}/proposals")).json()
    return proposals


async def _guest_view(client: AsyncClient, token: str) -> dict[str, Any]:
    view: dict[str, Any] = (await client.get(f"/api/guest/{token}")).json()
    return view


# --- the demo path -------------------------------------------------------------------------


async def test_a_replayed_warning_asks_then_the_whole_trip_cancellation_refunds_both_payments(
    operator: AsyncClient,
    client: AsyncClient,
    conn: Conn,
    paypal: FakePayPal,
    model: RaisingModel,
) -> None:
    trip = await _trip(operator, [GALE_RULE, WHOLE_TRIP_RULE])
    token = await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"])
    placed = len(paypal.calls)

    await _tick(conn, paypal, _now() + timedelta(minutes=11))
    asks = await _proposals(operator, trip["id"])
    asked_only = paypal.calls[placed:]
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    view = await _guest_view(client, token)

    assert [(a["kind"], a["subject"]) for a in asks] == [
        ("warning", "day:1:Gale Warning"),
        ("warning", "day:2:Gale Warning"),
        ("warning", "day:3:Gale Warning"),
    ]
    assert asked_only == []
    assert (proposal["state"], proposal["decided_by"], proposal["reasons"]) == (
        "accepted",
        "mandate",
        [],
    )
    assert (view["deposit"]["state"], view["deposit"]["refunded_minor"]) == ("captured", 5000)
    assert view["balance"]["payment"]["refunded_minor"] == 30000
    assert _moved(paypal, "refund") == 2
    final = await _proposals(operator, trip["id"])
    assert {p["kind"]: p["state"] for p in final if p["kind"] == "settlement"} == {
        "settlement": "executed"
    }
    assert {p["state"] for p in final if p["kind"] == "warning"} == {"answered"}
    commands = await (
        await conn.execute("select kind, proposal_id from money_commands where kind <> 'open'")
    ).fetchall()
    assert sorted((c["kind"], str(c["proposal_id"])) for c in commands) == [
        ("refund", proposal["id"]),
        ("refund", proposal["id"]),
    ]
    assert model.calls == 0


async def test_a_warning_without_a_cancellation_never_moves_money(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE, WHOLE_TRIP_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"])
    placed = len(paypal.calls)

    for minutes in (0, 11, 22):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))

    assert paypal.calls[placed:] == []
    assert {p["kind"] for p in await _proposals(operator, trip["id"])} == {"warning"}


# --- matching ------------------------------------------------------------------------------


async def test_a_day_cancellation_under_a_matched_warning_rule_settles_that_day(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"])

    proposal = await _cancel(operator, trip["id"], "day", 2)

    assert (proposal["state"], proposal["reasons"]) == ("accepted", [])
    assert proposal["parts"]["balance"][0]["lines"][0]["amount_minor"] == 10000
    matched = proposal["facts"]["clauses"][0]["matched"][0]
    assert matched["event"] == "Gale Warning"
    # the decision saw only evidence visible before it
    recorded = datetime.fromisoformat(proposal["facts"]["cancellation"]["recorded_at"])
    assert datetime.fromisoformat(matched["visible_at"]) < recorded
    # the day's money moves at decision (step 8b): a third of each payment
    await _tick(conn, paypal)
    refunds = await (
        await conn.execute(
            "select amount_minor from money_commands where kind = 'refund' and status = 'succeeded'"
            " order by amount_minor"
        )
    ).fetchall()
    assert [r["amount_minor"] for r in refunds] == [1666, 10000]


async def test_a_cancellation_under_a_warning_rule_with_no_matching_warning_asks(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])

    proposal = await _cancel(operator, trip["id"], "day", 2)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["WARNING_NOT_MATCHED"])
    assert [o["label"] for o in proposal["parts"]["balance"]] == ["refund 100%", "no remedy"]


async def test_a_warning_value_outside_the_confirmed_set_never_matches(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    advisory = replace(GALE, identifier="sca-1", event="Small Craft Advisory")
    await _replayed(conn, trip["id"], advisory)

    await _tick(conn, paypal, _now() + timedelta(minutes=11))
    proposal = await _cancel(operator, trip["id"], "day", 2)

    assert await _proposals(operator, trip["id"]) == [proposal]
    assert proposal["reasons"] == ["WARNING_NOT_MATCHED"]


async def test_evidence_visible_only_after_the_cancellation_does_not_match(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"], lands=_now() + timedelta(minutes=5))

    proposal = await _cancel(operator, trip["id"], "day", 2)

    assert proposal["reasons"] == ["WARNING_NOT_MATCHED"]
    assert proposal["facts"]["clauses"][0]["matched"] == []


async def test_a_warning_in_the_area_with_a_value_outside_the_catalogue_asks(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"], replace(GALE, identifier="x-1", event="Seiche Warning"))

    await _tick(conn, paypal, _now() + timedelta(minutes=11))
    asks = await _proposals(operator, trip["id"])

    assert {a["subject"] for a in asks} == {f"day:{d}:Seiche Warning" for d in (1, 2, 3)}
    assert all(a["facts"]["reason"] == "UNKNOWN_EVENT" for a in asks)


async def test_the_same_warning_seen_by_two_checks_and_its_update_asks_once_per_day(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE], days=1)
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"])

    await _tick(conn, paypal, _now() + timedelta(minutes=11))
    first = await _proposals(operator, trip["id"])
    await _tick(conn, paypal, _now() + timedelta(minutes=22))
    update = replace(GALE, identifier="gale-2", msg_type="Update", supersedes=(GALE.identifier,))
    await _replayed(conn, trip["id"], update)
    await _tick(conn, paypal, _now() + timedelta(minutes=33))

    asks = await _proposals(operator, trip["id"])
    assert [(a["kind"], a["subject"]) for a in first] == [("warning", "day:1:Gale Warning")]
    assert [a["id"] for a in asks] == [first[0]["id"]]
    runs = await (
        await conn.execute(
            "select count(*) as n from due_actions where kind = 'check_trip' and state = 'done'"
        )
    ).fetchone()
    assert runs is not None
    assert runs["n"] == 4  # the periodic check ran again after each completed run


async def test_an_unpaid_balance_asks_at_its_due_date_and_not_before(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    starts = _today() + timedelta(days=40)
    trip = await _trip(operator, [GALE_RULE], starts_on=starts.isoformat())
    await _guest(operator, client, conn, paypal, trip["id"])  # Ana pays both
    await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})
    due_at = datetime.combine(starts - timedelta(days=30), datetime.min.time(), ZoneInfo(ZONE))

    await _tick(conn, paypal, due_at - timedelta(minutes=1))
    before = await _proposals(operator, trip["id"])
    checks = await (
        await conn.execute("select count(*) as n from due_actions where kind = 'check_trip'")
    ).fetchone()
    await _tick(conn, paypal, due_at + timedelta(minutes=1))
    after = await _proposals(operator, trip["id"])

    assert before == []
    assert checks is not None
    assert checks["n"] == 0  # forty days out, nothing polls the trip
    assert [(a["kind"], a["facts"]["guest"], a["facts"]["due_minor"]) for a in after] == [
        ("unpaid_balance", "Ben", 30000)
    ]
    assert after[0]["facts"]["due_on"] == (starts - timedelta(days=30)).isoformat()


async def test_a_guest_invited_after_its_due_date_is_asked_about_a_day_later(
    operator: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])  # due 30 days before tomorrow: long past
    await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})

    await _tick(conn, paypal, _now() + timedelta(hours=23))
    before = await _proposals(operator, trip["id"])
    await _tick(conn, paypal, _now() + timedelta(hours=24, minutes=1))
    after = await _proposals(operator, trip["id"])

    assert [a["kind"] for a in before] == []
    assert [(a["kind"], a["facts"]["guest"]) for a in after] == [("unpaid_balance", "Ben")]


# --- what the policy leaves open, and the mandate -------------------------------------------


async def test_a_cause_no_rule_covers_asks_and_the_operator_chooses(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    token = await _guest(operator, client, conn, paypal, trip["id"])
    response = await operator.post(
        f"/api/trips/{trip['id']}/cancellations",
        json={"scope": "rest", "trip_day": 1, "cause": "safety"},
    )
    proposal = response.json()

    await _tick(conn, paypal)
    untouched = await _guest_view(client, token)
    decided = await operator.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/decision",
        json={"accept": {"deposit": 2, "balance": 1}},
    )
    await _tick(conn, paypal)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["NO_RULE"])
    assert untouched["balance"]["payment"]["refunded_minor"] == 0
    assert decided.json()["decided_by"] == "operator"
    assert (await _guest_view(client, token))["balance"]["payment"]["refunded_minor"] == 30000
    credits = await (await conn.execute("select purpose, amount_minor from credits")).fetchall()
    assert [(c["purpose"], c["amount_minor"]) for c in credits] == [("deposit", 5000)]
    assert _moved(paypal, "refund") == 1


async def test_a_clause_ruled_in_free_text_always_asks(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    # A version as publish stores one confirmed from a compilation: its ruling quotes the
    # whole-trip clause and was answered in free text.
    quote = "full refund of the trip"
    start = WORDING.index(quote)
    version = await (
        await conn.execute(
            "insert into policy_versions (workspace_id, wording, rules, rulings)"
            " select workspace_id, wording, rules, %s from policy_versions where id = %s"
            " returning id",
            (
                Jsonb(
                    [
                        {
                            "topic": "deduction_unspecified",
                            "question": "Which fee is deducted?",
                            "quote": quote,
                            "span": [start, start + len(quote)],
                            "answer": "minus a 50 USD fee",
                        }
                    ]
                ),
                trip["policy_version_id"],
            ),
        )
    ).fetchone()
    assert version is not None
    ruled = await _trip(operator, [WHOLE_TRIP_RULE], policy_version_id=str(version["id"]))
    await _guest(operator, client, conn, paypal, ruled["id"])

    proposal = await _cancel(operator, ruled["id"], "rest", 1)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["RULED_IN_TEXT"])


async def test_returning_more_than_the_mandate_asks(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE], mandate_guest_minor=1000)
    await _guest(operator, client, conn, paypal, trip["id"])

    proposal = await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["MANDATE_GUEST_CAP"])
    assert _moved(paypal, "refund") == 0


async def test_rejecting_a_settlement_moves_no_money(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE], mandate_trip_minor=0)
    await _guest(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)

    rejected = await operator.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/decision", json={"reject": True}
    )
    again = await operator.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/decision", json={"reject": True}
    )
    await _tick(conn, paypal)
    recorded_again = await _cancel(operator, trip["id"], "rest", 1)

    assert rejected.json()["state"] == "rejected"
    assert (again.status_code, again.json()) == (409, {"detail": "PROPOSAL_ALREADY_DECIDED"})
    assert _moved(paypal, "refund") == 0
    # rejecting withdraws the cancellation, so the operator can record it again
    assert recorded_again["state"] == "asked"


# --- outcomes ------------------------------------------------------------------------------


async def test_a_credit_remedy_records_a_deposit_credit_with_its_expiry(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    credit = _rule(
        scope="per_trip",
        quote=WHOLE_TRIP_RULE["quote"],
        remedies=[{"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": 12}],
    )
    trip = await _trip(operator, [credit])
    await _guest(operator, client, conn, paypal, trip["id"])

    proposal = await _cancel(operator, trip["id"], "rest", 1)

    rows = await (
        await conn.execute("select purpose, amount_minor, expires_at from credits order by purpose")
    ).fetchall()
    # both payments are captured, so both credits are written at acceptance (step 8b)
    assert proposal["state"] == "executed"
    assert [(r["purpose"], r["amount_minor"]) for r in rows] == [
        ("balance", 30000),
        ("deposit", 5000),
    ]
    decided = datetime.fromisoformat(proposal["decided_at"])
    assert {r["expires_at"] for r in rows} == {decided.replace(year=decided.year + 1)}
    assert _moved(paypal, "refund") == 0


async def test_the_operator_sees_each_guests_credits(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    credit = _rule(
        scope="per_trip",
        quote=WHOLE_TRIP_RULE["quote"],
        remedies=[{"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": 12}],
    )
    trip = await _trip(operator, [credit])
    await _guest(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)

    response = await operator.get(f"/api/trips/{trip['id']}/credits")

    guest_id = (await operator.get(f"/api/trips/{trip['id']}")).json()["guests"][0]["id"]
    credits = sorted(response.json(), key=lambda c: c["purpose"])
    assert [(c["guest_id"], c["purpose"], c["amount_minor"]) for c in credits] == [
        (guest_id, "balance", 30000),
        (guest_id, "deposit", 5000),
    ]
    assert {c["proposal_id"] for c in credits} == {proposal["id"]}


async def test_two_day_cancellations_refund_one_payment_twice_never_above_its_capture(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [_rule(quote=WHOLE_TRIP_RULE["quote"])])
    token = await _guest(operator, client, conn, paypal, trip["id"])

    day = await _cancel(operator, trip["id"], "day", 2)
    await _tick(conn, paypal)
    rest = await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)

    assert day["parts"]["deposit"][0]["lines"][0]["amount_minor"] == 1666
    assert rest["parts"]["deposit"][0]["lines"][0]["amount_minor"] == 3333
    assert rest["parts"]["balance"][0]["lines"][0]["amount_minor"] == 20000
    view = await _guest_view(client, token)
    assert (view["deposit"]["refunded_minor"], view["balance"]["payment"]["refunded_minor"]) == (
        4999,
        30000,
    )
    refunds = await (
        await conn.execute(
            "select p.purpose, c.amount_minor from money_commands c"
            " join payments p on p.id = c.payment_id"
            " where c.kind = 'refund' and c.status = 'succeeded' order by c.created_at"
        )
    ).fetchall()
    assert [(r["purpose"], r["amount_minor"]) for r in refunds] == [
        ("deposit", 1666),
        ("balance", 10000),
        ("deposit", 3333),
        ("balance", 20000),
    ]
    guest = (await operator.get(f"/api/trips/{trip['id']}")).json()["guests"][0]
    assert (guest["paid_minor"], guest["refunded_minor"], guest["released"]) == (35000, 34999, False)


async def test_the_rest_of_a_trip_after_an_executed_day_refund_returns_the_full_remainder(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """A day refunded under the per-day rule, then the whole trip under the per-trip rule:
    the second refund is exactly what the first left, so nothing is refused for the bound."""
    trip = await _trip(operator, [_rule(), WHOLE_TRIP_RULE])
    token = await _guest(operator, client, conn, paypal, trip["id"])

    await _cancel(operator, trip["id"], "day", 2)
    await _tick(conn, paypal)
    rest = await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)

    assert rest["parts"]["deposit"][0]["lines"][0]["amount_minor"] == 3334
    assert rest["parts"]["balance"][0]["lines"][0]["amount_minor"] == 20000
    view = await _guest_view(client, token)
    assert (view["deposit"]["refunded_minor"], view["balance"]["payment"]["refunded_minor"]) == (
        5000,
        30000,
    )
    assert "money_refused" not in {p["kind"] for p in await _proposals(operator, trip["id"])}


async def test_the_rest_of_a_trip_waits_for_an_open_settlement_ask(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    await _cancel(operator, trip["id"], "day", 2)  # asks: no matching warning

    response = await operator.post(
        f"/api/trips/{trip['id']}/cancellations",
        json={"scope": "rest", "trip_day": 1, "cause": "weather"},
    )
    twice = await operator.post(
        f"/api/trips/{trip['id']}/cancellations",
        json={"scope": "day", "trip_day": 2, "cause": "weather"},
    )

    assert (response.status_code, response.json()) == (409, {"detail": "SETTLEMENT_ASK_OPEN"})
    assert (twice.status_code, twice.json()) == (409, {"detail": "DAY_ALREADY_CANCELLED"})


async def test_a_refused_refund_becomes_an_ask(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    paypal.refuse.add("refund")

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)

    asks = await _proposals(operator, trip["id"])
    assert sorted((p["kind"], p["state"], p["facts"].get("amount_minor")) for p in asks) == [
        ("money_refused", "asked", 5000),
        ("money_refused", "asked", 30000),
        ("settlement", "executed", None),
    ]
    assert _moved(paypal, "refund") == 0


async def _row_lock_waiters(conn: Conn) -> int:
    row = await (
        await conn.execute(
            "select count(*) as n from pg_stat_activity where wait_event_type = 'Lock'"
            " and query like '%%from payments where id%%for update%%'"
        )
    ).fetchone()
    return int(row["n"]) if row else 0


@pytest.mark.parametrize("percent", [50, 100])
async def test_a_proposal_refunds_a_payment_once_when_settled_twice(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, percent: int
) -> None:
    """Two runs of one settlement both find no refund yet and both wait on the deposit's row:
    the second to get it finds the first's refund and leaves the sending to it, whether or
    not a second refund would still fit the capture (50%: it would; 100%: it would not)."""
    remedy = {"kind": "refund", "percent": percent, "deductions": [], "credit_expiry_months": None}
    trip = await _trip(operator, [{**WHOLE_TRIP_RULE, "remedies": [remedy]}])
    await _guest(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    assert proposal["state"] == "accepted"
    deposit = await (
        await conn.execute(
            "select deposit_payment_id as id from guests where trip_id = %s", (trip["id"],)
        )
    ).fetchone()
    assert deposit is not None
    database_url = make_conninfo(conn.info.dsn, password=conn.info.password)

    async def run() -> None:
        async with await connect(database_url) as other:
            await execute.settle(other, paypal.client(), UUID(proposal["id"]), _now())

    async with await connect(database_url) as holder:
        async with holder.transaction():
            await holder.execute(
                "select 1 from payments where id = %s for update", (deposit["id"],)
            )
            racing = [asyncio.create_task(run()) for _ in range(2)]
            for _ in range(100):
                if await _row_lock_waiters(conn) == 2:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("the settlements never waited on the deposit's row")
        results = await asyncio.gather(*racing, return_exceptions=True)
    # a run steps aside for the other (the deposit's refund, and the balance's) and asks to
    # run again; nothing fails
    assert {type(r).__name__ for r in results} <= {"NoneType", "RetryLater"}
    assert "RetryLater" in {type(r).__name__ for r in results}
    await execute.settle(conn, paypal.client(), UUID(proposal["id"]), _now())

    refunds = await (
        await conn.execute(
            "select amount_minor, status from money_commands where kind = 'refund'"
            " and payment_id = %s",
            (deposit["id"],),
        )
    ).fetchall()
    assert [(r["amount_minor"], r["status"]) for r in refunds] == [(50 * percent, "succeeded")]
    assert paypal.posts("/refund") == 2  # the deposit once, and the balance
    # the database refuses a second live refund for the decision too, whoever writes it
    with pytest.raises(psycopg.errors.UniqueViolation):
        await conn.execute(
            "insert into money_commands (id, payment_id, kind, target_id, amount_minor,"
            " request_id, invoice_id, status, proposal_id)"
            " select gen_random_uuid(), payment_id, kind, target_id, 1, request_id || '-2',"
            " invoice_id || '-2', 'pending', proposal_id from money_commands where kind = 'refund'"
            " and payment_id = %s",
            (deposit["id"],),
        )


async def test_a_settlement_refund_sent_before_a_crash_is_read_and_finished(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """A run claimed the refund (it may have reached PayPal) and stopped before recording the
    answer: the next run does not send it blind, it has PayPal read, and the settlement ends."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    deposit = await (
        await conn.execute(
            "select deposit_payment_id as id from guests where trip_id = %s", (trip["id"],)
        )
    ).fetchone()
    assert deposit is not None
    async with conn.transaction():
        refund = await commands.record_refund(
            conn, deposit["id"], amount_minor=5000, now=_now(), proposal_id=UUID(proposal["id"])
        )
        await commands.claim(conn, refund["id"])

    with pytest.raises(due.RetryLater):
        await execute.settle(conn, paypal.client(), UUID(proposal["id"]), _now())
    reconcile = await (
        await conn.execute(
            "select 1 from due_actions where kind = 'reconcile_payment' and payment_id = %s"
            " and state = 'pending'",
            (deposit["id"],),
        )
    ).fetchone()
    for minutes in (2, 4, 6):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))

    assert reconcile is not None
    assert paypal.posts("/refund") == 2  # the deposit's, read then sent once; the balance's
    assert {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])} == {
        "settlement": "executed"
    }


async def _deposits(conn: Conn, trip_id: str) -> list[dict[str, Any]]:
    """Each guest's deposit, in the order settlement visits the guests."""
    rows = await (
        await conn.execute(
            "select g.name, p.id, p.capture_id, p.refunded_minor from guests g"
            " join payments p on p.id = g.deposit_payment_id where g.trip_id = %s"
            " order by g.created_at, g.id",
            (trip_id,),
        )
    ).fetchall()
    return [dict(r) for r in rows]


async def _age_capture(conn: Conn, payment_id: UUID, days: int) -> None:
    """The capture happened `days` ago (the window counts from it)."""
    await conn.execute(
        "update payments set captured_at = now() - make_interval(days => %s) where id = %s",
        (days, payment_id),
    )


async def _settle_actions(conn: Conn) -> list[str]:
    rows = await (
        await conn.execute("select state from due_actions where kind = 'settle_proposal'")
    ).fetchall()
    return [r["state"] for r in rows]


async def test_a_refund_refused_when_recorded_asks_and_the_other_guests_are_refunded(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """Ana, visited first, paid her deposit over 180 days ago: her refund is refused before
    PayPal and asked about with the reason; Ben is refunded; the settlement stays accepted, and
    running it again changes nothing."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _guest(operator, client, conn, paypal, trip["id"])
    ben = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})
    token = ben.json()["guests"][-1]["token"]
    assert (await client.post(f"/api/guest/{token}/deposit/test-card")).status_code == 200
    assert (await client.post(f"/api/guest/{token}/balance/test-card")).status_code == 200
    ana, _ = await _deposits(conn, trip["id"])
    await _age_capture(conn, ana["id"], 181)

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    await due.schedule(
        conn,
        "settle_proposal",
        UUID((await _proposals(operator, trip["id"]))[0]["id"]),
        due_at=None,
    )
    await _tick(conn, paypal, _now() + timedelta(minutes=2))

    ana, ben_deposit = await _deposits(conn, trip["id"])
    asks = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "money_refused"]
    states = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert (ana["refunded_minor"], ben_deposit["refunded_minor"]) == (0, 5000)
    assert [a["facts"]["code"] for a in asks] == ["REFUND_WINDOW_CLOSED"]
    assert states["settlement"] == "accepted"
    assert await _settle_actions(conn) == ["done", "done"]
    assert not [c for c in paypal.calls if ana["capture_id"] in c]
    assert paypal.posts("/refund") == 3  # Ben's deposit, and both balances


async def test_a_pending_refund_is_read_until_paypal_completes_it(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """PayPal keeps the refund PENDING for more reads than a failing action would get; it is
    read until PayPal completes it, sent once, and the settlement then ends."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    paypal.refund_status = "PENDING"
    await _cancel(operator, trip["id"], "rest", 1)

    now = _now()
    for _ in range(30):
        await _tick(conn, paypal, now)
        if paypal.reads("/v2/payments/refunds/") > due.MAX_ATTEMPTS:
            paypal.settle_refunds()
        if {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])} == {
            "settlement": "executed"
        }:
            break
        now += timedelta(hours=1, minutes=1)
    else:
        pytest.fail("the settlement never ended")

    [deposit] = await _deposits(conn, trip["id"])
    failed = await (
        await conn.execute("select kind from due_actions where state = 'failed'")
    ).fetchall()
    assert paypal.reads("/v2/payments/refunds/") > due.MAX_ATTEMPTS
    assert deposit["refunded_minor"] == 5000
    assert paypal.posts("/refund") == 1
    assert failed == []


@pytest.mark.parametrize("by", ["settlement", "reconciliation"])
async def test_a_refund_whose_window_closes_before_its_first_send_is_not_sent(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, by: str
) -> None:
    """Recorded inside the window, not sent (the run stopped), and the window closed since:
    nothing goes to PayPal, the operator is asked with the reason, and the refund no longer
    holds the capture or the settlement's one live refund."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    [deposit] = await _deposits(conn, trip["id"])
    async with conn.transaction():
        refund = await commands.record_refund(
            conn, deposit["id"], amount_minor=5000, now=_now(), proposal_id=UUID(proposal["id"])
        )
    await _age_capture(conn, deposit["id"], 181)

    if by == "reconciliation":
        await service.reconcile(conn, paypal.client(), deposit["id"])
    await execute.settle(conn, paypal.client(), UUID(proposal["id"]), _now())

    row = await (
        await conn.execute(
            "select status, error_code, sent_at from money_commands where id = %s", (refund["id"],)
        )
    ).fetchone()
    asks = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "money_refused"]
    assert row is not None
    assert (row["status"], row["error_code"], row["sent_at"]) == (
        "rejected",
        "REFUND_WINDOW_CLOSED",
        None,
    )
    assert paypal.posts("/refund") == 0
    assert [a["facts"]["code"] for a in asks] == ["REFUND_WINDOW_CLOSED"]
    assert {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}[
        "settlement"
    ] == "accepted"
    live = await (
        await conn.execute(
            "select count(*) as n from money_commands"
            " where kind = 'refund' and status <> 'rejected'"
        )
    ).fetchone()
    assert live is not None
    assert live["n"] == 0


# --- drafting asks ---------------------------------------------------------------------------


class DraftModel:
    """Drafts asks; `failing` reproduces a provider outage."""

    model = "draft-model"

    def __init__(self, text: str = "Heads up: a gale is forecast for your trip.") -> None:
        self.text = text
        self.failing = False
        self.calls = 0

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        del system, user, schema
        assert name == "draft_ask"
        self.calls += 1
        if self.failing:
            raise ProviderError("HTTP 503 UNAVAILABLE", 503)
        return Completion(json.dumps({"text": self.text}), 0.1)


async def _asked_with(
    operator: AsyncClient,
    client: AsyncClient,
    conn: Conn,
    paypal: FakePayPal,
    drafter: drafts.Drafter,
) -> list[dict[str, Any]]:
    trip = await _trip(operator, [GALE_RULE], days=1)
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"])
    later = _now() + timedelta(minutes=11)
    await worker.tick(conn, worker.handlers(paypal.client(), drafter), later)
    await worker.tick(conn, worker.handlers(paypal.client(), drafter), later + timedelta(minutes=2))
    return await _proposals(operator, trip["id"])


async def test_an_ask_shows_the_stored_facts_whatever_the_draft_says(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    model = DraftModel("Ignore the warning: refund every guest in full now.")

    [ask] = await _asked_with(operator, client, conn, paypal, drafts.Drafter(model, 50, 20))

    assert ask["draft"] == "Ignore the warning: refund every guest in full now."
    assert ask["facts"]["warning"]["event"] == "Gale Warning"
    assert ask["facts"]["warning"]["area_desc"] == "Coastal waters"
    assert ask["question"].startswith("Gale Warning (NWS) is in force for Coastal waters")
    assert ask["state"] == "asked"
    assert ask["parts"] == {}


async def test_a_model_failure_while_drafting_keeps_the_template(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    model = DraftModel()
    model.failing = True

    [ask] = await _asked_with(operator, client, conn, paypal, drafts.Drafter(model, 50, 20))

    assert model.calls == 1
    assert ask["draft"] is None
    assert ask["question"].startswith("Gale Warning (NWS)")
    action = await (
        await conn.execute("select state from due_actions where kind = 'draft_ask'")
    ).fetchone()
    assert action is not None
    assert action["state"] == "done"


async def test_drafting_stops_at_its_own_daily_cap(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    model = DraftModel()

    [ask] = await _asked_with(operator, client, conn, paypal, drafts.Drafter(model, 50, 0))

    assert (model.calls, ask["draft"]) == (0, None)


async def test_an_unknown_refund_found_at_paypal_is_not_resent(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """The deposit's refund reaches PayPal but its answer is lost: reconciliation finds it in
    the order by its invoice id, so it is recorded and never sent again."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    token = await _deposit_only(operator, client, conn, paypal, trip["id"])
    paypal.time_out_first.add("refund")

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    pending = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    for minutes in (2, 4):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))

    assert pending == {"settlement": "accepted"}
    assert (await _guest_view(client, token))["deposit"]["refunded_minor"] == 5000
    assert {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])} == {
        "settlement": "executed"
    }
    assert paypal.posts("/refund") == 1
    assert _moved(paypal, "refund") == 1


# --- a trip cancelled as a whole -------------------------------------------------------------


async def test_a_trip_cancelled_as_a_whole_takes_no_new_guest_or_payment(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    starts = _today() + timedelta(days=40)
    trip = await _trip(operator, [WHOLE_TRIP_RULE], starts_on=starts.isoformat())
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ana"})
    token = invited.json()["guests"][0]["token"]
    assert (await client.post(f"/api/guest/{token}/deposit/test-card")).status_code == 200

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    late_invite = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Bo"})
    late_pay = await client.post(f"/api/guest/{token}/balance/test-card")

    assert (await _guest_view(client, token))["balance"]["payment"]["state"] == "pending"
    assert (late_invite.status_code, late_invite.json()) == (409, {"detail": "TRIP_CANCELLED"})
    assert (late_pay.status_code, late_pay.json()) == (409, {"detail": "TRIP_CANCELLED"})


async def _balance_id(conn: Conn, trip_id: str, name: str = "Ana") -> UUID:
    row = await (
        await conn.execute(
            "select balance_payment_id as id from guests where trip_id = %s and name = %s",
            (trip_id, name),
        )
    ).fetchone()
    assert row is not None
    balance: UUID = row["id"]
    return balance


async def _lock_waiters(conn: Conn) -> int:
    """Sessions waiting on a lock held by an attempt being claimed."""
    row = await (
        await conn.execute(
            "select count(*) as n from pg_stat_activity where wait_event_type = 'Lock'"
            " and query like '%%update money_commands%%'"
        )
    ).fetchone()
    return int(row["n"]) if row else 0


async def _payment(conn: Conn, payment_id: UUID) -> dict[str, Any]:
    row = await (
        await conn.execute("select * from payments where id = %s", (payment_id,))
    ).fetchone()
    assert row is not None
    return dict(row)


async def test_a_checkout_completing_during_a_whole_trip_cancellation_is_refused_or_settled(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """Ana's checkout is being completed (its attempt claimed, in another connection's open
    transaction) while the operator cancels the trip: the cancellation's waiver waits on the
    attempt, finds it at PayPal and leaves the balance to the settlement, which refunds it once
    captured. Ben's checkout, completed after the cancellation, finds its attempt closed and
    is refused; his balance is waived, never charged."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    tokens = {}
    for name in ("Ana", "Ben"):
        invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": name})
        tokens[name] = invited.json()["guests"][-1]["token"]
        assert (
            await client.post(f"/api/guest/{tokens[name]}/deposit/test-card")
        ).status_code == 200
    orders = {
        name: (await client.post(f"/api/guest/{token}/balance/checkout")).json()["order_id"]
        for name, token in tokens.items()
    }
    ana = await _balance_id(conn, trip["id"], "Ana")
    attempt = await commands.checkout_attempt(conn, ana, orders["Ana"])
    database_url = make_conninfo(conn.info.dsn, password=conn.info.password)

    async with await connect(database_url) as completing:
        async with completing.transaction():
            await commands.claim(completing, attempt, first_send=True)
            cancelling = asyncio.create_task(_cancel(operator, trip["id"], "rest", 1))
            for _ in range(100):
                if await _lock_waiters(conn) >= 1:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("the cancellation never waited on Ana's attempt")
        proposal = await cancelling
    waited = await _payment(conn, ana)
    # the claim committed first: the attempt is at PayPal, and it completes
    await service.execute(conn, paypal.client(), attempt)
    refused = await client.post(f"/api/guest/{tokens['Ben']}/balance/checkout/{orders['Ben']}")
    await _tick(conn, paypal, _now() + timedelta(minutes=1))
    await _tick(conn, paypal, _now() + timedelta(minutes=2))

    assert proposal["state"] == "accepted"
    assert (waited["state"], waited["waived_minor"]) == ("pending", 0)
    assert (refused.status_code, refused.json()) == (409, {"detail": "TRIP_CANCELLED"})
    paid = await _payment(conn, ana)
    assert (paid["captured_minor"], paid["refunded_minor"]) == (30000, 30000)
    ben = await _payment(conn, await _balance_id(conn, trip["id"], "Ben"))
    assert (ben["state"], ben["waived_minor"]) == ("pending", 30000)
    with pytest.raises(Conflict, match="PAYMENT_ATTEMPT_CLOSED"):
        await service.complete_checkout(conn, paypal.client(), ben["id"], orders["Ben"])
    assert {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])} == {
        "settlement": "executed"
    }


async def test_the_rest_of_a_trip_from_a_cancelled_day_covers_the_days_after_it(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [_rule(quote=WHOLE_TRIP_RULE["quote"])])
    await _guest(operator, client, conn, paypal, trip["id"])
    await _cancel(operator, trip["id"], "day", 2)

    rest = await _cancel(operator, trip["id"], "rest", 2)

    assert rest["facts"]["cancellation"]["covered_days"] == [3]
    assert rest["facts"]["cancellation"]["whole_trip"] is False
    assert rest["parts"]["balance"][0]["lines"][0]["amount_minor"] == 10000


async def test_a_payment_captured_after_its_decision_is_settled_by_that_decision(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """A day refunded and a day credited while Ana's balance is unpaid: the refunded day is
    taken off what she owes, and the credited day is charged; once she pays, the same decision
    writes her credit. No new proposal, and nothing is refunded."""
    credit = {"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": None}
    trip = await _trip(operator, [_rule(remedies=[REFUND_ALL, credit], chooser="operator")])
    token = await _deposit_only(operator, client, conn, paypal, trip["id"])
    refund_day = await _cancel(operator, trip["id"], "day", 1)
    credit_day = await _cancel(operator, trip["id"], "day", 2)
    for proposal, option in ((refund_day, 0), (credit_day, 1)):
        decided = await operator.post(
            f"/api/trips/{trip['id']}/proposals/{proposal['id']}/decision",
            json={"accept": {"deposit": option, "balance": option}},
        )
        assert decided.json()["state"] == "accepted", decided.text
    before = await _guest_view(client, token)
    await _tick(conn, paypal)

    paid = await client.post(f"/api/guest/{token}/balance/test-card")
    await _tick(conn, paypal, _now() + timedelta(minutes=1))

    assert before["balance"]["payment"]["due_minor"] == 20000
    assert paid.json()["balance"]["payment"]["captured_minor"] == 20000
    credits = await (await conn.execute("select purpose, amount_minor from credits")).fetchall()
    assert sorted((c["purpose"], c["amount_minor"]) for c in credits) == [
        ("balance", 10000),
        ("deposit", 1666),
    ]
    states = [(p["kind"], p["state"]) for p in await _proposals(operator, trip["id"])]
    assert states == [("settlement", "executed"), ("settlement", "executed")]
    assert paypal.posts("/refund") == 1  # the deposit's refunded day only


async def test_a_withdrawn_cancellation_reopens_its_warning_asks_and_the_check(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE, WHOLE_TRIP_RULE], mandate_trip_minor=0)
    await _guest(operator, client, conn, paypal, trip["id"])
    await _replayed(conn, trip["id"])
    await _tick(conn, paypal, _now() + timedelta(minutes=11))
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    answered = {
        a["state"] for a in await _proposals(operator, trip["id"]) if a["kind"] == "warning"
    }

    await operator.post(
        f"/api/trips/{trip['id']}/proposals/{proposal['id']}/decision", json={"reject": True}
    )
    reopened = {
        a["state"] for a in await _proposals(operator, trip["id"]) if a["kind"] == "warning"
    }
    await _replayed(conn, trip["id"], replace(GALE, identifier="x-2", event="Seiche Warning"))
    await _tick(conn, paypal, _now() + timedelta(minutes=22))

    subjects = {a["subject"] for a in await _proposals(operator, trip["id"])}
    assert answered == {"answered"}
    assert reopened == {"asked"}
    assert {f"day:{d}:Seiche Warning" for d in (1, 2, 3)} <= subjects


# --- what the console reads (plan step 9) --------------------------------------------------


async def _evidence(operator: AsyncClient, trip_id: str) -> dict[str, Any]:
    response = await operator.get(f"/api/trips/{trip_id}/evidence")
    assert response.status_code == 200, response.text
    evidence: dict[str, Any] = response.json()
    return evidence


async def test_evidence_shows_a_replayed_warning_once_visible_with_its_label(
    operator: AsyncClient, conn: Conn
) -> None:
    trip = await _trip(operator, [GALE_RULE], starts_on=_today().isoformat(), days=2)
    assert (await _evidence(operator, trip["id"]))["warnings"] == []

    await _replayed(conn, trip["id"], lands=_now() + timedelta(hours=1))
    assert (await _evidence(operator, trip["id"]))["warnings"] == []

    await _replayed(conn, trip["id"])
    [warning] = (await _evidence(operator, trip["id"]))["warnings"]
    assert warning["event"] == "Gale Warning"
    assert warning["replayed"] is True
    assert warning["recorded_at"] == GALE.sent.isoformat().replace("+00:00", "Z")
    assert warning["trip_days"] == [1, 2]


async def test_evidence_corroborates_with_a_forecast_per_local_day_fetched_once_an_hour(
    operator: AsyncClient, forecast: Any
) -> None:
    trip = await _trip(operator, [GALE_RULE], starts_on=_today().isoformat(), days=2)

    first = (await _evidence(operator, trip["id"]))["forecast"]
    again = (await _evidence(operator, trip["id"]))["forecast"]

    assert len(forecast.requests) == 1
    assert forecast.requests[0].url.params["timezone"] == ZONE
    assert first == again
    assert first["status"] == "ok"
    assert [d["day"] for d in first["days"]] == [
        _today().isoformat(),
        (_today() + timedelta(days=1)).isoformat(),
    ]
    assert first["days"][0]["max_wave_height_m"] == "1.50"
    assert "not for coastal navigation" in first["attribution"]


async def test_evidence_without_a_forecast_still_shows_the_warnings(
    operator: AsyncClient, conn: Conn, forecast: Any
) -> None:
    forecast.fail = True
    trip = await _trip(operator, [GALE_RULE], starts_on=_today().isoformat())
    await _replayed(conn, trip["id"])

    evidence = await _evidence(operator, trip["id"])

    assert evidence["forecast"] == {
        "status": "unavailable",
        "days": [],
        "fetched_at": None,
        "url": None,
        "attribution": evidence["forecast"]["attribution"],
    }
    assert len(evidence["warnings"]) == 1


async def _recorded(conn: Conn, warning: OfficialWarning = GALE) -> str:
    """`warning` recorded long ago, as the recorder's files are read in (step 7)."""
    await store.record(conn, warning, warning.sent)
    row = await (
        await conn.execute(
            "select id from official_warnings where identifier = %s", (warning.identifier,)
        )
    ).fetchone()
    assert row is not None
    return str(row["id"])


async def test_replaying_a_recorded_warning_opens_its_ask_before_answering(
    operator: AsyncClient, conn: Conn
) -> None:
    trip = await _trip(operator, [GALE_RULE], starts_on=_today().isoformat(), days=2)
    warning_id = await _recorded(conn)

    response = await operator.post(
        f"/api/trips/{trip['id']}/replays", json={"warning_id": warning_id}
    )

    assert response.status_code == 201, response.text
    [warning] = response.json()["warnings"]
    assert warning["replayed"] is True
    asks = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "warning"]
    assert sorted(a["subject"] for a in asks) == ["day:1:Gale Warning", "day:2:Gale Warning"]


async def test_a_replay_is_refused_for_another_source_or_a_cancelled_trip(
    operator: AsyncClient, conn: Conn
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE], starts_on=_today().isoformat())
    warning_id = await _recorded(conn)
    other = await _recorded(conn, replace(GALE, source="meteoalarm", identifier="ma-1"))
    path = f"/api/trips/{trip['id']}/replays"

    mismatch = await operator.post(path, json={"warning_id": other})
    unknown = await operator.post(path, json={"warning_id": str(UUID(int=1))})
    await _cancel(operator, trip["id"], "rest", 1)
    cancelled = await operator.post(path, json={"warning_id": warning_id})

    assert (mismatch.status_code, mismatch.json()["detail"]) == (400, "WARNING_SOURCE_MISMATCH")
    assert unknown.status_code == 404
    assert (cancelled.status_code, cancelled.json()["detail"]) == (409, "TRIP_CANCELLED")


async def test_recorded_warnings_are_listed_by_source_and_area_newest_first(
    operator: AsyncClient, conn: Conn
) -> None:
    newer = replace(
        GALE, identifier="gale-2", sent=GALE.sent + timedelta(days=1), area_codes=("PZZ999",)
    )
    elsewhere = replace(GALE, source="meteoalarm", identifier="ma-1")
    for warning in (GALE, newer, elsewhere):
        await _recorded(conn, warning)

    every = await operator.get("/api/recorded-warnings", params={"source": "nws"})
    one_area = await operator.get("/api/recorded-warnings", params={"source": "nws", "area": AREA})

    assert [w["identifier"] for w in every.json()] == ["gale-2", GALE.identifier]
    assert [w["identifier"] for w in one_area.json()] == [GALE.identifier]
    assert every.json()[0]["recorded_at"] == newer.sent.isoformat().replace("+00:00", "Z")


SUBJECT_OF = {"check_trip": 0, "reconcile_payment": 1, "settle_proposal": 2}


async def _failed(conn: Conn, kind: str, subject: Any, error: str, at: datetime) -> None:
    """A due action that gave up after its retries."""
    subjects: list[Any] = [None, None, None]
    subjects[SUBJECT_OF[kind]] = subject
    await conn.execute(
        "insert into due_actions (kind, trip_id, payment_id, proposal_id, due_at, state,"
        " attempts, last_error, finished_at) values (%s, %s, %s, %s, %s, 'failed', 8, %s, %s)",
        (kind, *subjects, at, error, at),
    )


async def test_failed_actions_of_a_trip_are_grouped_per_kind_and_subject(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    other = await _trip(operator, [WHOLE_TRIP_RULE])
    token = await _guest(operator, client, conn, paypal, trip["id"])
    guest = await (
        await conn.execute("select deposit_payment_id from guests where token = %s", (token,))
    ).fetchone()
    assert guest is not None
    proposal = await _cancel(operator, trip["id"], "day", 1)
    then = _now() - timedelta(hours=2)
    await _failed(conn, "check_trip", trip["id"], "ValueError", then)
    await _failed(conn, "check_trip", trip["id"], "KeyError", then + timedelta(hours=1))
    await _failed(conn, "check_trip", other["id"], "ValueError", then)
    unknown = "PAYPAL_OUTCOME_STILL_UNKNOWN"
    await _failed(conn, "reconcile_payment", guest["deposit_payment_id"], unknown, then)
    await _failed(conn, "settle_proposal", proposal["id"], "RuntimeError", then)

    response = await operator.get(f"/api/trips/{trip['id']}/failed-actions")

    assert response.status_code == 200, response.text
    assert sorted((a["kind"], a["failures"], a["last_error"]) for a in response.json()) == [
        ("check_trip", 2, "KeyError"),
        ("reconcile_payment", 1, "PAYPAL_OUTCOME_STILL_UNKNOWN"),
        ("settle_proposal", 1, "RuntimeError"),
    ]


async def _settlement_view(client: AsyncClient, token: str) -> dict[str, Any]:
    response = await client.get(f"/api/guest/{token}/settlement")
    assert response.status_code == 200, response.text
    view: dict[str, Any] = response.json()
    return view


async def test_a_guest_sees_the_cancellation_the_warning_the_clause_and_its_own_outcome(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    ana = await _guest(operator, client, conn, paypal, trip["id"])
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})
    ben = invited.json()["guests"][-1]["token"]
    assert (await client.post(f"/api/guest/{ben}/deposit/test-card")).status_code == 200
    await _replayed(conn, trip["id"])
    await _cancel(operator, trip["id"], "day", 2)

    response = await client.get(f"/api/guest/{ana}/settlement")
    [settlement] = response.json()["settlements"]

    assert settlement["state"] == "accepted"
    assert (settlement["days"], settlement["cause"]) == ([2], "weather")
    [warning] = settlement["warnings"]
    assert (warning["event"], warning["replayed"]) == ("Gale Warning", True)
    assert warning["recorded_at"] == GALE.sent.isoformat().replace("+00:00", "Z")
    assert [o["purpose"] for o in settlement["outcomes"]] == ["deposit", "balance"]
    deposit = settlement["outcomes"][0]
    assert (deposit["kind"], deposit["amount_minor"], deposit["clauses"]) == (
        "refund",
        1666,
        [GALE_CLAUSE],
    )
    # the sentence is narrative only: amounts come from the stored lines, never from text a
    # model may draft in its place (step 11; the model never supplies amounts)
    assert settlement["explanation"] == (
        "The operator cancelled day 2 for weather. Gale Warning (NWS) was in force."
    )
    # only this guest: no other guest's name or amount, and no internal ids
    assert "Ben" not in response.text
    assert trip["id"] not in response.text
    assert all(g["id"] not in response.text for g in (await _proposals(operator, trip["id"])))


async def test_a_guest_sees_a_settlement_being_decided_without_amounts_and_not_once_rejected(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE], mandate_guest_minor=1000)
    token = await _guest(operator, client, conn, paypal, trip["id"])
    asked = await _cancel(operator, trip["id"], "rest", 1)

    [settlement] = (await _settlement_view(client, token))["settlements"]

    assert (settlement["state"], settlement["outcomes"]) == ("asked", [])
    assert settlement["explanation"] == (
        "The operator cancelled the whole trip for weather."
        " The operator is deciding what this means for your payments."
    )
    decided = await operator.post(
        f"/api/trips/{trip['id']}/proposals/{asked['id']}/decision", json={"reject": True}
    )
    assert decided.status_code == 200, decided.text
    # rejecting withdraws the cancellation (step 8a)
    assert (await _settlement_view(client, token))["settlements"] == []


async def test_a_guest_sees_its_credit(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    credit = _rule(
        scope="per_trip",
        quote=WHOLE_TRIP_RULE["quote"],
        remedies=[{"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": 12}],
    )
    trip = await _trip(operator, [credit])
    token = await _guest(operator, client, conn, paypal, trip["id"])
    await _cancel(operator, trip["id"], "rest", 1)

    view = await _settlement_view(client, token)

    assert sorted((c["purpose"], c["amount_minor"]) for c in view["credits"]) == [
        ("balance", 30000),
        ("deposit", 5000),
    ]
    assert view["credits"][0]["expires_at"] is not None
    outcomes = view["settlements"][0]["outcomes"]
    assert [(o["purpose"], o["kind"], o["amount_minor"], o["status"]) for o in outcomes] == [
        ("deposit", "credit", 5000, "credited"),
        ("balance", "credit", 30000, "credited"),
    ]
    assert (await client.get("/api/guest/unknown-token/settlement")).status_code == 404


async def test_evidence_keeps_a_live_warning_and_its_replay_apart(
    operator: AsyncClient, conn: Conn
) -> None:
    trip = await _trip(operator, [GALE_RULE], starts_on=_today().isoformat())
    fetched = _now() - timedelta(hours=2)
    live = replace(
        GALE, sent=fetched, onset=fetched, expires=_now() + timedelta(days=3)
    )  # fetched live two hours ago, still in force
    await _replayed(conn, trip["id"], live)

    warnings = (await _evidence(operator, trip["id"]))["warnings"]

    assert sorted(w["replayed"] for w in warnings) == [False, True]


async def test_a_guest_sees_a_live_warning_and_its_replay_apart(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE], starts_on=_today().isoformat())
    token = await _guest(operator, client, conn, paypal, trip["id"])
    fetched = _now() - timedelta(hours=2)
    live = replace(GALE, sent=fetched, onset=fetched, expires=_now() + timedelta(days=3))
    await _replayed(conn, trip["id"], live)
    await _cancel(operator, trip["id"], "day", 1)

    [settlement] = (await _settlement_view(client, token))["settlements"]

    assert sorted(w["replayed"] for w in settlement["warnings"]) == [False, True]


async def _deposit_id(conn: Conn, trip_id: str) -> UUID:
    row = await (
        await conn.execute(
            "select deposit_payment_id as id from guests where trip_id = %s", (trip_id,)
        )
    ).fetchone()
    assert row is not None
    deposit: UUID = row["id"]
    return deposit


async def test_a_settlement_waiting_on_a_pending_refund_asks_when_paypal_fails_it(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """The read that ends the refund FAILED runs while the settlement's action is running, so
    its wake is dropped; the settlement still runs again and asks the operator, with PayPal's
    reason."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    paypal.refund_status = "PENDING"
    await _cancel(operator, trip["id"], "rest", 1)
    deposit = await _deposit_id(conn, trip["id"])
    handlers = worker.handlers(paypal.client())

    async def settle_while_paypal_fails_it(c: Conn, action: due.Action, now: datetime) -> None:
        try:
            await handlers["settle_proposal"](c, action, now)
        finally:
            paypal.settle_refunds("FAILED")
            assert await service.reconcile(c, paypal.client(), deposit) != {}

    now = _now()
    await due.run_due(conn, {**handlers, "settle_proposal": settle_while_paypal_fails_it}, now)
    assert await _settle_actions(conn) == ["pending"]  # one action: the read's wake was dropped
    await _tick(conn, paypal, now + service.PENDING_REFUND_READ_EVERY + timedelta(minutes=1))

    asks = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "money_refused"]
    assert [a["facts"]["code"] for a in asks] == ["PAYPAL_REFUND_FAILED"]
    assert await _settle_actions(conn) == ["done"]


@pytest.mark.parametrize("ended", ["done", "failed"])
async def test_a_settlement_reads_a_refund_whose_read_was_dropped(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, ended: str
) -> None:
    """The refund's answer is lost while the deposit's `reconcile_payment` is running, so the
    send's wake is dropped and that run ends without reading it (`failed`: for another command
    of the payment): the settlement has it read, and ends."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    deposit = await _deposit_id(conn, trip["id"])
    await due.schedule(conn, "reconcile_payment", deposit, due_at=_now())
    await conn.execute(
        "update due_actions set state = 'running', attempts = 1,"
        " lease_until = now() + interval '1 minute' where payment_id = %s",
        (deposit,),
    )
    paypal.time_out_first.add("refund")

    with pytest.raises(due.RetryLater):
        await execute.settle(conn, paypal.client(), UUID(proposal["id"]), _now())
    await conn.execute(
        "update due_actions set state = %s, lease_until = null, finished_at = now()"
        " where payment_id = %s",
        (ended, deposit),
    )
    now = _now()
    for _ in range(due.MAX_ATTEMPTS):
        now += timedelta(minutes=31)
        await _tick(conn, paypal, now)

    states = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert states == {"settlement": "executed"}
    assert paypal.posts("/refund") == 1


async def test_a_settlement_whose_pending_refund_is_no_longer_read_fails(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """The refund's reads kept failing and its `reconcile_payment` gave up: the settlement does
    not wait on it forever, it fails too, for the operator console."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    paypal.refund_status = "PENDING"
    await _cancel(operator, trip["id"], "rest", 1)
    paypal.fail_reads.add("/v2/payments/refunds/")

    now = _now()
    for _ in range(4 * due.MAX_ATTEMPTS):
        await _tick(conn, paypal, now)
        if "failed" in await _settle_actions(conn):
            break
        now += timedelta(minutes=31)
    else:
        pytest.fail("the settlement waited on a refund nobody reads")

    failed = await (
        await conn.execute("select kind from due_actions where state = 'failed' order by kind")
    ).fetchall()
    assert [r["kind"] for r in failed] == ["reconcile_payment", "settle_proposal"]
    assert paypal.posts("/refund") == 1


async def test_an_older_failed_read_does_not_stop_a_settlement_whose_refund_is_read(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """An earlier `reconcile_payment` of the deposit failed; the refund PayPal now processes is
    read by a newer one, so the settlement waits on that read instead of failing."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    proposal = await _cancel(operator, trip["id"], "rest", 1)
    deposit = await _deposit_id(conn, trip["id"])
    await due.schedule(conn, "reconcile_payment", deposit, due_at=_now())
    await conn.execute(
        "update due_actions set state = 'failed', finished_at = now() where payment_id = %s",
        (deposit,),
    )
    paypal.refund_status = "PENDING"

    with pytest.raises(due.Waiting):
        await execute.settle(conn, paypal.client(), UUID(proposal["id"]), _now())
    assert await service.reconcile(conn, paypal.client(), deposit) != {}  # the newer one reads it
    with pytest.raises(due.Waiting):
        await execute.settle(conn, paypal.client(), UUID(proposal["id"]), _now())

    states = await (
        await conn.execute(
            "select state from due_actions where payment_id = %s order by id", (deposit,)
        )
    ).fetchall()
    assert [r["state"] for r in states] == ["failed", "pending"]


# --- step 8b: money at decision, unpaid balances, undecided commands -------------------------


async def _decide(
    operator: AsyncClient, trip_id: str, proposal_id: str, body: dict[str, Any]
) -> Any:
    return await operator.post(f"/api/trips/{trip_id}/proposals/{proposal_id}/decision", json=body)


async def test_the_balance_due_after_a_refunded_day_is_reduced_by_that_refund(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [_rule()])
    token = await _deposit_only(operator, client, conn, paypal, trip["id"])
    order = (await client.post(f"/api/guest/{token}/balance/checkout")).json()["order_id"]

    day = await _cancel(operator, trip["id"], "day", 2)
    stale = await client.post(f"/api/guest/{token}/balance/checkout/{order}")
    due_view = await _guest_view(client, token)
    paid = await client.post(f"/api/guest/{token}/balance/test-card")
    await _tick(conn, paypal)

    assert day["state"] == "accepted"  # the deposit's day is refunded through PayPal
    assert due_view["balance"]["payment"]["due_minor"] == 20000
    # the checkout opened for the full balance closed when the day was taken off it
    assert (stale.status_code, stale.json()) == (409, {"detail": "PAYMENT_ATTEMPT_CLOSED"})
    balance = paid.json()["balance"]["payment"]
    assert (balance["captured_minor"], balance["refunded_minor"]) == (20000, 0)
    outcomes = (await _settlement_view(client, token))["settlements"][0]["outcomes"]
    assert [(o["purpose"], o["status"]) for o in outcomes] == [
        ("deposit", "refunded"),
        ("balance", "not_charged"),
    ]


async def test_nothing_due_refuses_a_checkout(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """Every day cancelled one by one, each refunded in full: the unpaid balance owes nothing,
    though the trip was never cancelled as a whole."""
    trip = await _trip(operator, [_rule()])
    token = await _deposit_only(operator, client, conn, paypal, trip["id"])
    for day in (1, 2, 3):
        await _cancel(operator, trip["id"], "day", day)

    refused = await client.post(f"/api/guest/{token}/balance/test-card")

    assert (await _guest_view(client, token))["balance"]["payment"]["due_minor"] == 0
    assert (refused.status_code, refused.json()) == (409, {"detail": "NOTHING_DUE"})


async def test_waived_amounts_count_toward_the_mandate(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE], mandate_guest_minor=5000)
    await _deposit_only(operator, client, conn, paypal, trip["id"])

    proposal = await _cancel(operator, trip["id"], "rest", 1)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["MANDATE_GUEST_CAP"])


async def _unpaid_ask(operator: AsyncClient, conn: Conn, paypal: FakePayPal, trip_id: str) -> Any:
    await _tick(conn, paypal, _now() + timedelta(hours=24, minutes=1))
    asks = [p for p in await _proposals(operator, trip_id) if p["kind"] == "unpaid_balance"]
    assert len(asks) == 1
    return asks[0]


async def test_a_released_guest_is_left_out_of_later_settlements(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [_rule()])
    await _guest(operator, client, conn, paypal, trip["id"])  # Ana pays both
    ben = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})
    ben_token = ben.json()["guests"][-1]["token"]
    ask = await _unpaid_ask(operator, conn, paypal, trip["id"])

    released = await _decide(operator, trip["id"], ask["id"], {"release": True})
    pay = await client.post(f"/api/guest/{ben_token}/balance/test-card")
    day = await _cancel(operator, trip["id"], "day", 2)

    assert released.json()["state"] == "answered"
    assert (pay.status_code, pay.json()) == (409, {"detail": "GUEST_RELEASED"})
    rows = (await operator.get(f"/api/trips/{trip['id']}")).json()["guests"]
    assert [(g["name"], g["released"], g["paid_minor"]) for g in rows] == [
        ("Ana", False, 35000),
        ("Ben", True, 0),
    ]
    view = await _guest_view(client, ben_token)
    assert view["released"] is True
    assert view["balance"]["payment"]["due_minor"] == 0
    names = await (await conn.execute("select id, name from guests")).fetchall()
    ids = {str(g["id"]): g["name"] for g in names}
    assert [ids[x["guest_id"]] for x in day["parts"]["balance"][0]["lines"]] == ["Ana"]
    assert day["parts"]["balance"][0]["lines"][0]["amount_minor"] == 10000


async def test_an_ask_stays_open_until_the_operator_answers(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """The unpaid-balance ask stays asked when the guest pays meanwhile; nothing is captured or
    settled for it, and releasing a guest who has paid is refused."""
    trip = await _trip(operator, [_rule()])
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})
    token = invited.json()["guests"][-1]["token"]
    ask = await _unpaid_ask(operator, conn, paypal, trip["id"])
    placed = len(paypal.calls)

    for minutes in (11, 22):
        await _tick(conn, paypal, _now() + timedelta(hours=24, minutes=minutes))
    still = [p for p in await _proposals(operator, trip["id"]) if p["id"] == ask["id"]]
    asked_only = paypal.calls[placed:]
    assert (await client.post(f"/api/guest/{token}/balance/test-card")).status_code == 200
    late = await _decide(operator, trip["id"], ask["id"], {"release": True})
    waited = await _decide(operator, trip["id"], ask["id"], {"dismiss": True})

    assert [p["state"] for p in still] == ["asked"]
    assert asked_only == []
    assert (late.status_code, late.json()) == (409, {"detail": "BALANCE_PAID"})
    assert waited.json()["state"] == "dismissed"


async def test_a_refund_past_the_window_asks_with_its_reason(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    (ana,) = await _deposits(conn, trip["id"])
    await _age_capture(conn, ana["id"], 181)

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)

    (ask,) = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "money_refused"]
    assert ask["facts"]["code"] == "REFUND_WINDOW_CLOSED"
    assert "180 days" in ask["question"]
    assert "credit" in ask["question"]
    assert paypal.posts("/refund") == 0


async def test_a_refused_resend_that_paypal_cannot_settle_becomes_an_ask(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """The refund's first send is lost, a read finds nothing, and PayPal refuses the resend:
    no read can decide it, so the operator is asked; dismissing would leave it open, so only
    an answer from PayPal's records is taken, and `not found` turns it into a refused refund."""
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await _deposit_only(operator, client, conn, paypal, trip["id"])
    paypal.lose_first.add("refund")

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    paypal.refuse.add("refund")
    for minutes in (2, 4, 6):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))
    (ask,) = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "command_open"]
    dismissed = await _decide(operator, trip["id"], ask["id"], {"dismiss": True})
    answered = await _decide(operator, trip["id"], ask["id"], {"resolve": "not_found"})
    await _tick(conn, paypal, _now() + timedelta(minutes=8))

    assert (ask["facts"]["kind"], ask["facts"]["amount_minor"]) == ("refund", 5000)
    assert (dismissed.status_code, dismissed.json()) == (
        409,
        {"detail": "COMMAND_OPEN_TAKES_NO_DISMISS"},
    )
    assert answered.json()["state"] == "answered"
    kinds = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert kinds == {"settlement": "executed", "command_open": "answered", "money_refused": "asked"}
    command = await (
        await conn.execute("select status, error_code from money_commands where kind = 'refund'")
    ).fetchone()
    assert command is not None
    assert (command["status"], command["error_code"]) == (
        "rejected",
        "OPERATOR_FOUND_NOTHING_AT_PAYPAL",
    )
    failed = await (
        await conn.execute("select 1 from due_actions where state = 'failed'")
    ).fetchone()
    assert failed is None


async def test_a_cancellation_recorded_after_the_trip_is_settled_like_any_other(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    starts = _today() - timedelta(days=10)
    trip = await _trip(operator, [_rule()], starts_on=starts.isoformat())
    token = await _guest(operator, client, conn, paypal, trip["id"])

    proposal = await _cancel(operator, trip["id"], "day", 1)
    await _tick(conn, paypal)

    assert proposal["state"] == "accepted"
    view = await _guest_view(client, token)
    assert (view["deposit"]["refunded_minor"], view["balance"]["payment"]["refunded_minor"]) == (
        1666,
        10000,
    )


async def test_a_balance_paid_after_its_day_was_waived_is_not_refunded_for_that_day(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    """The day is taken off Ana's unpaid balance at decision; she pays the reduced balance
    before the settlement runs for her deposit: the day is not refunded on top (found by the
    closing review)."""
    trip = await _trip(operator, [_rule()])
    token = await _deposit_only(operator, client, conn, paypal, trip["id"])
    day = await _cancel(operator, trip["id"], "day", 1)
    paid = await client.post(f"/api/guest/{token}/balance/test-card")
    for minutes in (0, 1, 2):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))

    assert day["state"] == "accepted"  # the deposit's day still goes to PayPal
    balance = (await _guest_view(client, token))["balance"]["payment"]
    assert (paid.status_code, balance["captured_minor"], balance["refunded_minor"]) == (
        200,
        20000,
        0,
    )
    states = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert states == {"settlement": "executed"}
    assert paypal.posts("/refund") == 1  # the deposit's day only


async def test_a_refund_the_operator_finds_at_paypal_is_recorded_and_its_settlement_ends(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    token = await _deposit_only(operator, client, conn, paypal, trip["id"])
    paypal.lose_first.add("refund")
    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    paypal.refuse.add("refund")
    for minutes in (2, 4, 6):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))
    (ask,) = [p for p in await _proposals(operator, trip["id"]) if p["kind"] == "command_open"]

    answered = await _decide(operator, trip["id"], ask["id"], {"resolve": "found"})
    await _tick(conn, paypal, _now() + timedelta(minutes=8))

    assert answered.json()["state"] == "answered"
    assert (await _guest_view(client, token))["deposit"]["refunded_minor"] == 5000
    states = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert states == {"settlement": "executed", "command_open": "answered"}
    command = await (
        await conn.execute("select status, result from money_commands where kind = 'refund'")
    ).fetchone()
    assert command is not None
    assert (command["status"], command["result"]["resolvedBy"]) == ("succeeded", "operator")
