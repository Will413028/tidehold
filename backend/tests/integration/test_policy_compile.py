"""Compiling wording through the model under the daily cap, and publishing from a compilation
(plan step 5 acceptance)."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.fake_model import FakeModel
from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import POLICY, TRIP, WORDING, post_trip, trip_starting
from tidehold.bootstrap import worker
from tidehold.bootstrap.app import create_app
from tidehold.platform.db import Conn
from tidehold.platform.settings import Settings

BENCH = Path(__file__).resolve().parents[3] / "bench"
LABELS = json.loads((BENCH / "labels.json").read_text())["fixtures"]
# The labels leave a warning condition's quote out (it is not scored); the fixture model
# quotes it as a model would.
WARNING_QUOTES = {
    "pure-dive": "unless there has been an official storm warning issued by the local authorities"
}


def _wording(fixture: str) -> str:
    wording: str = json.loads((BENCH / "fixtures" / f"{fixture}.json").read_text())["wording"]
    return wording


def _model_rules(fixture: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = json.loads(json.dumps(LABELS[fixture]["rules"]))
    for rule in rules:
        if rule["warning"] is not None:
            rule["warning"]["quote"] = WARNING_QUOTES[fixture]
    return rules


async def _compile(operator: AsyncClient, wording: str) -> dict[str, Any]:
    response = await operator.post("/api/policy-compilations", json={"wording": wording})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _calls(conn: Conn) -> list[tuple[str, str]]:
    rows = await (
        await conn.execute("select mode, status from model_calls order by created_at")
    ).fetchall()
    return [(r["mode"], r["status"]) for r in rows]


@pytest.mark.parametrize("fixture", ["pure-dive", "blue-ribbon", "mikes-dauin", "scuba-tribe"])
async def test_the_dev_fixtures_compile_to_their_labelled_rules_and_rulings(
    fixture: str, operator: AsyncClient, model: FakeModel
) -> None:
    wording = _wording(fixture)
    # A deduction ruling quotes the deduction it rules (Scuba Tribe's fee); the labels score
    # topics only.
    model.answer(
        wording,
        _model_rules(fixture),
        LABELS[fixture]["needs_ruling"],
        {"deduction_conditional": SCUBA_FEE},
    )

    compiled = await _compile(operator, wording)

    assert (compiled["mode"], compiled["model"]) == ("live", "fake-model")
    assert [r["rule"] for r in compiled["rules"]] == _model_rules(fixture)
    assert [r["clause"] for r in compiled["rules"]] == [r["quote"] for r in _model_rules(fixture)]
    assert [r["topic"] for r in compiled["needs_ruling"]] == LABELS[fixture]["needs_ruling"]


SCUBA_FEE = "minus 3.5% processing fee for online payments"
# A fee stated twice: the rule's own deduction is the second occurrence.
FEE_WORDING = (
    "Fees: online payments carry a 3.5% processing fee. Weather: if we cancel the trip, you "
    "receive a full refund minus 3.5% processing fee."
)


@dataclass(frozen=True)
class Case:
    """What the fake model compiles: the rules, the ruling topics and their quotes."""

    wording: str
    rules: list[dict[str, Any]]
    topics: list[str]
    quotes: dict[str, str]
    product: str | None


def _scuba_tribe(fee_quote: str | None) -> Case:
    quotes = {} if fee_quote is None else {"deduction_conditional": fee_quote}
    topics = LABELS["scuba-tribe"]["needs_ruling"]
    return Case(_wording("scuba-tribe"), _model_rules("scuba-tribe"), topics, quotes, "Fun Dives:")


Settle = Callable[[Case], Awaitable[dict[str, Any]]]


@pytest.fixture
def settle_compiled(
    operator: AsyncClient, client: AsyncClient, conn: Conn, paypal: FakePayPal, model: FakeModel
) -> Settle:
    async def settle(case: Case) -> dict[str, Any]:
        """Compile `case`, check that publishing waits for the deduction rulings'
        answers, answer every ruling, bind a trip with one paid deposit, and cancel all of it
        for weather; the settlement's proposal."""
        model.answer(case.wording, case.rules, case.topics, case.quotes)
        compiled = await _compile(operator, case.wording)
        body = {"wording": case.wording, "compilation_id": compiled["id"], "rules": case.rules}
        asked_only = [
            {"index": i, "answer": "As written."}
            for i, t in enumerate(case.topics)
            if not t.startswith("deduction_")
        ]
        answers = [
            {"index": i, "answer": "No fee on card payments."} for i in range(len(case.topics))
        ]

        unanswered = await operator.post(
            "/api/policy-versions", json={**body, "rulings": asked_only}
        )
        version = await operator.post("/api/policy-versions", json={**body, "rulings": answers})

        assert unanswered.json() == {"detail": "RULING_UNANSWERED"}
        assert version.status_code == 201, version.text
        tomorrow = datetime.now(ZoneInfo(TRIP["timezone"])).date() + timedelta(days=1)
        product = {} if case.product is None else {"policy_product": case.product}
        trip = await post_trip(
            operator, trip_starting(tomorrow), policy_version_id=version.json()["id"], **product
        )
        assert trip.status_code == 201, trip.text
        trip_id = trip.json()["id"]
        invited = await operator.post(f"/api/trips/{trip_id}/guests", json={"name": "Ana"})
        token = invited.json()["guests"][-1]["token"]
        assert (await client.post(f"/api/guest/{token}/deposit/test-card")).status_code == 200
        await worker.tick(conn, worker.handlers(paypal.client()), datetime.now(UTC))
        calls = model.calls

        cancelled = await operator.post(
            f"/api/trips/{trip_id}/cancellations",
            json={"scope": "rest", "trip_day": 1, "cause": "weather"},
        )

        assert cancelled.status_code == 201, cancelled.text
        assert model.calls == calls  # settlement makes no model call
        proposal: dict[str, Any] = cancelled.json()
        return proposal

    return settle


async def test_a_conditional_deduction_needs_its_ruling_before_publish_and_its_settlement_asks(
    settle_compiled: Settle,
) -> None:
    proposal = await settle_compiled(_scuba_tribe(SCUBA_FEE))

    # Code finds the ruling's quote inside the rule's clause; the rule's two remedies ask too.
    assert (proposal["state"], proposal["reasons"]) == ("asked", ["REMEDY_CHOICE", "RULED_IN_TEXT"])


async def test_a_ruling_on_a_rules_fee_is_placed_in_that_rule_and_the_settlement_asks(
    settle_compiled: Settle,
) -> None:
    # One remedy, inside the mandate: without the ruling this settlement would be accepted.
    # The ruling's short quote occurs first in the "Fees:" passage; it is placed in the rule.
    rule = _one_fee_rule("minus 3.5% processing fee")
    case = Case(
        FEE_WORDING,
        [rule],
        ["deduction_conditional"],
        {"deduction_conditional": "3.5% processing fee"},
        None,
    )

    proposal = await settle_compiled(case)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["RULED_IN_TEXT"])


def _one_fee_rule(fee_quote: str) -> dict[str, Any]:
    """FEE_WORDING's weather rule: one remedy, a 3.5% deduction quoted `fee_quote`."""
    deduction = {"kind": "percent", "value": 3.5, "label": "processing fee", "quote": fee_quote}
    return {
        **POLICY["rules"][0],
        "scope": "per_trip",
        "remedies": [
            {
                "kind": "refund",
                "percent": 100,
                "deductions": [deduction],
                "credit_expiry_months": None,
            }
        ],
        "quote": "if we cancel the trip, you receive a full refund minus 3.5% processing fee.",
    }


async def test_an_unquoted_conditional_ruling_is_replaced_by_one_covering_every_deduction(
    settle_compiled: Settle, model: FakeModel
) -> None:
    # The model asks about the fee twice without quoting it: the compiler cannot tie the
    # question to a rule, so it becomes a span_not_found item that covers every deduction, and
    # the one-remedy rule (inside the mandate) still asks instead of deducting.
    rule = _one_fee_rule("minus 3.5% processing fee")
    case = Case(FEE_WORDING, [rule], ["deduction_conditional"], {}, None)

    proposal = await settle_compiled(case)

    assert model.calls == 2  # the answer and one retry naming the unplaced question
    assert (proposal["state"], proposal["reasons"]) == ("asked", ["RULED_IN_TEXT"])


async def test_an_unquoted_unspecified_deduction_ruling_makes_every_rule_ask(
    settle_compiled: Settle,
) -> None:
    # No figure, so no Deduction: the replacement covers every rule's clause, and the
    # one-remedy rule (inside the mandate) asks instead of refunding the full amount.
    rule = _one_fee_rule("minus 3.5% processing fee")
    rule["remedies"][0]["deductions"] = []
    case = Case(FEE_WORDING, [rule], ["deduction_unspecified"], {}, None)

    proposal = await settle_compiled(case)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["RULED_IN_TEXT"])


TWO_PRODUCTS = (
    "Day trips:\nIf we cancel a day trip for weather, you receive a full refund minus 3.5% "
    "processing fee for online payments.\nLiveaboards:\nIf we cancel a liveaboard for weather, "
    "you receive a full refund minus 3.5% processing fee for online payments."
)
# The first occurrence of "full refund less fees" runs past the day trip rule's quote; the
# second lies inside the liveaboard rule's.
STRADDLE = (
    "Day trips:\nIf we cancel a day trip you get a full refund less fees.\nLiveaboards:\n"
    "If we cancel a liveaboard you get a full refund less fees."
)


def _product_rule(product: str, quote: str, deductions: list[dict[str, Any]]) -> dict[str, Any]:
    remedy = {"kind": "refund", "percent": 100, "deductions": deductions}
    return {
        **POLICY["rules"][0],
        "product": product,
        "scope": "per_trip",
        "remedies": [{**remedy, "credit_expiry_months": None}],
        "quote": quote,
    }


def _fee(product: str) -> dict[str, Any]:
    return {"kind": "percent", "value": 3.5, "label": f"{product} fee", "quote": SCUBA_FEE}


@pytest.mark.parametrize("product", ["Day trips:", "Liveaboards:"])
async def test_one_ruling_on_a_fee_sentence_two_products_share_asks_on_each(
    product: str, settle_compiled: Settle
) -> None:
    day, liveaboard = TWO_PRODUCTS.split("\nLiveaboards:\n")
    rules = [
        _product_rule("Day trips:", day.removeprefix("Day trips:\n"), [_fee("day")]),
        _product_rule("Liveaboards:", liveaboard, [_fee("liveaboard")]),
    ]
    quotes = {"deduction_conditional": SCUBA_FEE}
    case = Case(TWO_PRODUCTS, rules, ["deduction_conditional"], quotes, product)

    proposal = await settle_compiled(case)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["RULED_IN_TEXT"])


@pytest.mark.parametrize("product", ["Day trips:", "Liveaboards:"])
async def test_a_ruling_quote_straddling_one_clause_and_inside_another_asks_on_both(
    product: str, settle_compiled: Settle
) -> None:
    rules = [
        _product_rule("Day trips:", "If we cancel a day trip you get a full refund", []),
        _product_rule(
            "Liveaboards:", "If we cancel a liveaboard you get a full refund less fees.", []
        ),
    ]
    quotes = {"deduction_unspecified": "full refund less fees"}
    case = Case(STRADDLE, rules, ["deduction_unspecified"], quotes, product)

    proposal = await settle_compiled(case)

    assert (proposal["state"], proposal["reasons"]) == ("asked", ["RULED_IN_TEXT"])


async def test_a_quote_the_wording_does_not_contain_never_becomes_a_rule(
    operator: AsyncClient, model: FakeModel
) -> None:
    fabricated = {**POLICY["rules"][0], "quote": "We refund 100% whenever a guest asks."}
    model.answer(WORDING, [fabricated], [])

    compiled = await _compile(operator, WORDING)

    assert compiled["rules"] == []
    assert [r["topic"] for r in compiled["needs_ruling"]] == ["span_not_found"]
    assert model.calls == 2  # the answer and one retry naming the missing quote


async def test_publishing_from_a_compilation_needs_every_ruling_answered(
    operator: AsyncClient, model: FakeModel
) -> None:
    wording = _wording("blue-ribbon")
    model.answer(wording, _model_rules("blue-ribbon"), ["credit_expiry_unspecified"])
    compiled = await _compile(operator, wording)
    body = {
        "wording": wording,
        "compilation_id": compiled["id"],
        "rules": _model_rules("blue-ribbon"),
    }

    unanswered = await operator.post("/api/policy-versions", json=body)
    answered = await operator.post(
        "/api/policy-versions",
        json={**body, "rulings": [{"index": 0, "answer": "Credit never expires."}]},
    )

    assert unanswered.json() == {"detail": "RULING_UNANSWERED"}
    version = answered.json()
    assert version["compilation_id"] == compiled["id"]
    assert [(r["topic"], r["answer"]) for r in version["rulings"]] == [
        ("credit_expiry_unspecified", "Credit never expires.")
    ]


async def test_a_compilation_is_published_only_with_its_own_wording(
    operator: AsyncClient, other_operator: AsyncClient
) -> None:
    compiled = await _compile(operator, WORDING)
    edited = {**POLICY, "wording": WORDING + " Thank you.", "compilation_id": compiled["id"]}

    other_wording = await operator.post("/api/policy-versions", json=edited)
    other_workspace = await other_operator.post(
        "/api/policy-versions", json={**POLICY, "compilation_id": compiled["id"]}
    )

    assert other_wording.json() == {"detail": "COMPILATION_NOT_FOR_WORDING"}
    assert other_workspace.status_code == 404


async def test_model_failure_falls_back_to_no_ai(
    operator: AsyncClient, model: FakeModel, conn: Conn
) -> None:
    model.failing = True

    compiled = await _compile(operator, WORDING)
    by_hand = await operator.post("/api/policy-versions", json=POLICY)

    assert (compiled["mode"], compiled["reason"], compiled["id"]) == (
        "no_ai",
        "AI_UNAVAILABLE",
        None,
    )
    assert compiled["rules"] == []
    assert await _calls(conn) == [("live", "failed")]
    error = await (await conn.execute("select error_code from model_calls")).fetchone()
    assert error is not None
    assert error["error_code"] == "ProviderError: HTTP 503 UNAVAILABLE"
    assert by_hand.status_code == 201


@pytest.mark.parametrize("daily_cap", [1])
async def test_daily_cap_replays_recorded_results_then_stops_live_calls(
    operator: AsyncClient, other_operator: AsyncClient, model: FakeModel, conn: Conn
) -> None:
    first = await _compile(operator, WORDING)
    replayed = await _compile(other_operator, WORDING)
    capped = await _compile(other_operator, "If we cancel, you get credit.")

    assert first["mode"] == "live"
    assert replayed["mode"] == "recorded"
    assert replayed["rules"] == first["rules"]
    assert replayed["id"] != first["id"]
    assert (capped["mode"], capped["reason"]) == ("no_ai", "AI_DAILY_CAP_REACHED")
    assert model.calls == 1
    assert await _calls(conn) == [
        ("live", "succeeded"),
        ("recorded", "succeeded"),
        ("capped", "failed"),
    ]
    # A replay is the replaying workspace's own compilation: it can publish from it.
    published = await other_operator.post(
        "/api/policy-versions", json={**POLICY, "compilation_id": replayed["id"]}
    )
    assert published.status_code == 201


async def test_a_failed_live_call_replays_a_recorded_result_for_the_same_input(
    operator: AsyncClient, model: FakeModel
) -> None:
    first = await _compile(operator, WORDING)
    model.failing = True

    again = await _compile(operator, WORDING)

    assert (again["mode"], again["rules"]) == ("recorded", first["rules"])


async def test_without_a_model_the_operator_writes_rules_by_hand(
    database_url: str, conn: Conn
) -> None:
    bare = create_app(Settings(database_url=database_url, public_origin="http://test"))
    async with AsyncClient(
        transport=ASGITransport(app=bare),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as browser:
        await browser.post("/api/workspaces")
        compiled = await _compile(browser, WORDING)
        proposal = await browser.post(
            "/api/event-set-proposals", json={"source": "nws", "quote": "a gale warning"}
        )

    assert (compiled["mode"], compiled["reason"]) == ("no_ai", "AI_NOT_CONFIGURED")
    assert (proposal.json()["mode"], proposal.json()["values"]) == ("no_ai", [])
    assert [
        "Small Craft Advisory",
        "Gale Warning",
        "Storm Warning",
        "Hurricane Force Wind Warning",
    ] in (proposal.json()["catalogue"])
    assert await _calls(conn) == []


async def test_a_proposed_event_set_keeps_only_the_sources_catalogue_values(
    operator: AsyncClient, model: FakeModel
) -> None:
    model.event_values = ["Gale Warning", "Storm Warning", "Tornado Warning"]

    response = await operator.post(
        "/api/event-set-proposals",
        json={"source": "nws", "quote": "a Gale Warning or stronger"},
    )
    unknown = await operator.post(
        "/api/event-set-proposals", json={"source": "bmkg", "quote": "a storm warning"}
    )

    assert response.status_code == 201
    assert (response.json()["mode"], response.json()["values"]) == (
        "live",
        ["Gale Warning", "Storm Warning"],
    )
    assert unknown.json() == {"detail": "WARNING_SOURCE_NOT_IN_CATALOGUE"}


async def test_a_compilation_can_be_read_back_by_its_workspace(operator: AsyncClient) -> None:
    compiled = await _compile(operator, WORDING)

    again = await operator.get(f"/api/policy-compilations/{compiled['id']}")

    assert again.json() == compiled
