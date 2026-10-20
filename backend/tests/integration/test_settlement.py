"""Settlement from the operator's cancellation, asks and the deposit (plan step 8a acceptance).

No settlement test touches a model provider: the compiler of this module's app raises on any
call, and evaluation is asserted to have made none.
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from psycopg.types.json import Jsonb

from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import TRIP, post_trip
from tidehold.bootstrap import worker
from tidehold.money import service
from tidehold.money.paypal import TEST_CARD
from tidehold.platform.db import Conn
from tidehold.policy.providers import Completion, ProviderError
from tidehold.settlement import drafts
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


async def _guest(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, trip_id: str
) -> str:
    """A guest who paid the deposit and authorized the balance (sandbox test card)."""
    invited = await operator.post(f"/api/trips/{trip_id}/guests", json={"name": "Ana"})
    token: str = invited.json()["guests"][-1]["token"]
    assert (await client.post(f"/api/guest/{token}/deposit/test-card")).status_code == 200
    await _tick(conn, paypal)
    held = await client.post(f"/api/guest/{token}/balance/test-card")
    assert held.json()["balance"]["payment"]["state"] == "authorized", held.text
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


async def test_a_replayed_warning_asks_then_the_whole_trip_cancellation_voids_the_hold(
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
    assert view["balance"]["payment"]["state"] == "voided"
    assert view["deposit"]["state"] == "captured"
    assert _moved(paypal, "refund") == 1
    assert _moved(paypal, "void") == 1
    final = await _proposals(operator, trip["id"])
    assert {p["kind"]: p["state"] for p in final if p["kind"] == "settlement"} == {
        "settlement": "executed"
    }
    assert {p["state"] for p in final if p["kind"] == "warning"} == {"answered"}
    commands = await (
        await conn.execute("select kind, proposal_id from money_commands where kind <> 'open'")
    ).fetchall()
    assert {(c["kind"], str(c["proposal_id"])) for c in commands} == {
        ("refund", proposal["id"]),
        ("void", proposal["id"]),
    }
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
    # a day cancellation moves no money before finalize (step 8b)
    assert _moved(paypal, "refund") == 0
    assert _moved(paypal, "void") == 0


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


async def test_a_guest_without_an_authorization_on_the_first_trip_day_is_asked_about(
    operator: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [GALE_RULE])
    await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ben"})
    day_1 = datetime.combine(_today() + timedelta(days=1), datetime.min.time(), ZoneInfo(ZONE))

    await _tick(conn, paypal, day_1 - timedelta(minutes=1))
    before = await _proposals(operator, trip["id"])
    await _tick(conn, paypal, day_1 + timedelta(minutes=10))
    after = await _proposals(operator, trip["id"])

    assert before == []
    assert [(a["kind"], a["facts"]["guest"]) for a in after] == [("unauthorized_guest", "Ben")]


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
    assert untouched["balance"]["payment"]["state"] == "authorized"
    assert decided.json()["decided_by"] == "operator"
    assert (await _guest_view(client, token))["balance"]["payment"]["state"] == "voided"
    credits = await (await conn.execute("select purpose, amount_minor from credits")).fetchall()
    assert [(c["purpose"], c["amount_minor"]) for c in credits] == [("deposit", 5000)]
    assert _moved(paypal, "refund") == 0


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
    assert _moved(paypal, "void") == 0


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
    assert _moved(paypal, "void") == 0
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
    await _tick(conn, paypal)

    row = await (await conn.execute("select amount_minor, expires_at from credits")).fetchone()
    assert proposal["state"] == "accepted"
    assert row is not None
    assert row["amount_minor"] == 5000
    assert row["expires_at"].date().year in (_today().year, _today().year + 1)
    assert _moved(paypal, "refund") == 0
    # the balance owes its full amount as credit: captured at finalize (step 8b), not voided
    assert _moved(paypal, "void") == 0


async def test_the_rest_of_a_trip_is_netted_against_an_accepted_day(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [_rule(quote=WHOLE_TRIP_RULE["quote"])])
    token = await _guest(operator, client, conn, paypal, trip["id"])

    day = await _cancel(operator, trip["id"], "day", 2)
    rest = await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)

    assert day["parts"]["deposit"][0]["lines"][0]["amount_minor"] == 1666
    assert rest["parts"]["deposit"][0]["lines"][0]["amount_minor"] == 3333
    assert rest["parts"]["balance"][0]["lines"][0]["amount_minor"] == 20000
    assert (await _guest_view(client, token))["balance"]["payment"]["state"] == "voided"
    refunds = await (
        await conn.execute("select amount_minor from money_commands where kind = 'refund'")
    ).fetchall()
    assert [r["amount_minor"] for r in refunds] == [3333]


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

    kinds = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert kinds == {"settlement": "executed", "money_refused": "asked"}
    assert _moved(paypal, "void") == 1


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


async def test_an_unknown_void_is_reconciled_and_the_settlement_resumes_without_resending(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    token = await _guest(operator, client, conn, paypal, trip["id"])
    paypal.time_out_first.add("void")

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)
    pending = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    for minutes in (2, 4):
        await _tick(conn, paypal, _now() + timedelta(minutes=minutes))

    assert pending == {"settlement": "accepted"}
    assert (await _guest_view(client, token))["balance"]["payment"]["state"] == "voided"
    assert {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])} == {
        "settlement": "executed"
    }
    voids = await (
        await conn.execute("select count(*) as n from money_commands where kind = 'void'")
    ).fetchone()
    assert voids is not None
    assert voids["n"] == 1
    assert _moved(paypal, "void") == 1


# --- a trip cancelled as a whole -------------------------------------------------------------


async def test_a_trip_cancelled_as_a_whole_takes_no_new_guest_hold_or_payment(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    starts = _today() + timedelta(days=40)
    trip = await _trip(operator, [WHOLE_TRIP_RULE], starts_on=starts.isoformat())
    invited = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ana"})
    token = invited.json()["guests"][0]["token"]
    assert (await client.post(f"/api/guest/{token}/deposit/test-card")).status_code == 200

    await _cancel(operator, trip["id"], "rest", 1)
    opens = datetime.combine(starts - timedelta(days=26), datetime.min.time(), UTC)
    await _tick(conn, paypal, opens + timedelta(hours=1))
    late_invite = await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Bo"})
    late_pay = await client.post(f"/api/guest/{token}/balance/test-card")

    assert (await _guest_view(client, token))["balance"]["payment"] is None
    assert (late_invite.status_code, late_invite.json()) == (409, {"detail": "TRIP_CANCELLED"})
    assert (late_pay.status_code, late_pay.json()) == (409, {"detail": "TRIP_CANCELLED"})


async def test_payments_placed_while_the_trip_was_being_cancelled_are_released_or_asked_about(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ana"})
    await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Bo"})
    await _tick(conn, paypal)  # Ana's and Bo's balances open, neither is placed
    guests = await (
        await conn.execute(
            "select name, deposit_payment_id, balance_payment_id from guests where trip_id = %s",
            (trip["id"],),
        )
    ).fetchall()
    by_name = {g["name"]: g for g in guests}

    proposal = await _cancel(operator, trip["id"], "rest", 1)
    # attempts already in flight when the operator cancelled complete afterwards
    await service.pay_with_test_card(
        conn, paypal.client(), by_name["Ana"]["balance_payment_id"], TEST_CARD, daily_cap=30
    )
    await service.pay_with_test_card(
        conn, paypal.client(), by_name["Bo"]["deposit_payment_id"], TEST_CARD, daily_cap=30
    )
    await _tick(conn, paypal)

    state = await (
        await conn.execute(
            "select state from payments where id = %s", (by_name["Ana"]["balance_payment_id"],)
        )
    ).fetchone()
    assert proposal["state"] == "accepted"
    assert state is not None
    assert state["state"] == "voided"
    kinds = {p["kind"]: p["state"] for p in await _proposals(operator, trip["id"])}
    assert kinds == {"settlement": "executed", "late_payment": "asked"}


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


async def test_a_payment_that_lands_after_the_settlement_ran_is_still_released(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal
) -> None:
    trip = await _trip(operator, [WHOLE_TRIP_RULE])
    await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ana"})
    await _tick(conn, paypal)
    balance = await (
        await conn.execute(
            "select balance_payment_id from guests where trip_id = %s", (trip["id"],)
        )
    ).fetchone()
    assert balance is not None

    await _cancel(operator, trip["id"], "rest", 1)
    await _tick(conn, paypal)  # the settlement runs and finds nothing placed
    await service.pay_with_test_card(
        conn, paypal.client(), balance["balance_payment_id"], TEST_CARD, daily_cap=30
    )
    await _tick(conn, paypal, _now() + timedelta(minutes=1))
    await _tick(conn, paypal, _now() + timedelta(minutes=2))

    state = await (
        await conn.execute(
            "select state from payments where id = %s", (balance["balance_payment_id"],)
        )
    ).fetchone()
    runs = await (
        await conn.execute("select count(*) as n from due_actions where kind = 'settle_proposal'")
    ).fetchone()
    assert state is not None
    assert state["state"] == "voided"
    assert runs is not None
    assert runs["n"] == 2  # the first run, and one more for the late hold


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
    assert settlement["explanation"] == (
        "The operator cancelled day 2 for weather. Gale Warning (NWS) was in force."
        " Your deposit: USD 16.66 refunded. Your balance: USD 100.00 released."
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

    assert [(c["purpose"], c["amount_minor"]) for c in view["credits"]] == [("deposit", 5000)]
    assert view["credits"][0]["expires_at"] is not None
    assert "Your deposit: USD 50.00 as credit" in view["settlements"][0]["explanation"]
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
