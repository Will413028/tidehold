"""Compiling wording through the model under the daily cap, and publishing from a compilation
(plan step 5 acceptance)."""

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.fake_model import FakeModel
from tests.integration.payloads import POLICY, WORDING
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


@pytest.mark.parametrize("fixture", ["pure-dive", "blue-ribbon", "mikes-dauin"])
async def test_the_dev_fixtures_compile_to_their_labelled_rules_and_rulings(
    fixture: str, operator: AsyncClient, model: FakeModel
) -> None:
    wording = _wording(fixture)
    model.answer(wording, _model_rules(fixture), LABELS[fixture]["needs_ruling"])

    compiled = await _compile(operator, wording)

    assert (compiled["mode"], compiled["model"]) == ("live", "fake-model")
    assert [r["rule"] for r in compiled["rules"]] == _model_rules(fixture)
    assert [r["clause"] for r in compiled["rules"]] == [r["quote"] for r in _model_rules(fixture)]
    assert [r["topic"] for r in compiled["needs_ruling"]] == LABELS[fixture]["needs_ruling"]


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
