"""The demo operator seed (plan step 9 unit 8) against PostgreSQL, through the API."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from tests.integration.payloads import POLICY
from tidehold.bootstrap import demo
from tidehold.platform.db import Conn
from tidehold.warnings import store

FIXTURES = Path(__file__).parent.parent / "fixtures" / "warnings"
# the committed Super Typhoon Rosal Signal #2 warning for Batanes
TYPHOON = "pagasa-20261021T030020026596Z-document-5e96e2e35935.json.gz"


async def _seed(operator: AsyncClient) -> dict[str, Any]:
    response = await operator.post("/api/demo-operator")
    assert response.status_code == 201, response.text
    seeded: dict[str, Any] = response.json()
    return seeded


async def test_the_seed_fills_an_empty_workspace_with_the_demo_operator(
    operator: AsyncClient,
) -> None:
    before = datetime.now(ZoneInfo("Asia/Manila")).date().isoformat()
    seeded = await _seed(operator)
    after = datetime.now(ZoneInfo("Asia/Manila")).date().isoformat()

    [version] = (await operator.get("/api/policy-versions")).json()
    assert version["compilation_id"] is None
    assert version["rulings"] == []
    assert version["warning_source"] == "pagasa"
    assert version["quoted_from"] == {
        "name": "Pure Dive Resort",
        "url": "https://purediveresort.com/deep-diving/",
        "accessed": "2026-10-19",
    }
    [rule] = version["rules"]
    assert rule["clause"] == demo.CLAUSE
    assert rule["warning_clause"] == "an official storm warning issued by the local authorities"
    trip = (await operator.get(f"/api/trips/{seeded['trip_id']}")).json()
    assert trip["starts_on"] in {before, after}  # today in Manila, even across its midnight
    assert (trip["timezone"], trip["warning_areas"]) == ("Asia/Manila", ["020900000"])
    # invited in one transaction, so their order is not fixed
    assert sorted((g["name"], g["paid_minor"]) for g in trip["guests"]) == [
        ("Guest A", 0),
        ("Guest B", 0),
    ]
    assert {g["token"] for g in trip["guests"]} == {g["token"] for g in seeded["guests"]}


async def test_the_seed_refuses_a_workspace_that_is_not_empty(
    operator: AsyncClient, other_operator: AsyncClient
) -> None:
    await _seed(operator)
    again = await operator.post("/api/demo-operator")
    published = await other_operator.post("/api/policy-versions", json=POLICY)
    over_a_policy = await other_operator.post("/api/demo-operator")

    assert (again.status_code, over_a_policy.status_code) == (409, 409)
    assert "WORKSPACE_NOT_EMPTY" in again.text
    assert len((await operator.get("/api/trips")).json()) == 1
    assert len((await operator.get("/api/policy-versions")).json()) == 1
    assert (await other_operator.get("/api/trips")).json() == []
    # an operator's own wording has no attribution
    assert published.json()["quoted_from"] is None


async def test_the_recorded_typhoon_asks_and_cancelling_the_trip_offers_refund_or_credit(
    operator: AsyncClient, client: AsyncClient, conn: Conn
) -> None:
    seeded = await _seed(operator)
    trip_id = seeded["trip_id"]
    for guest in seeded["guests"]:
        for purpose in ("deposit", "balance"):
            paid = await client.post(f"/api/guest/{guest['token']}/{purpose}/test-card")
            assert paid.status_code == 200, paid.text
    await store.ingest(conn, store.envelopes([FIXTURES / TYPHOON]))
    [recorded] = (await operator.get("/api/recorded-warnings", params={"source": "pagasa"})).json()

    replayed = await operator.post(
        f"/api/trips/{trip_id}/replays", json={"warning_id": recorded["id"]}
    )
    asks = (await operator.get(f"/api/trips/{trip_id}/proposals")).json()
    cancelled = await operator.post(
        f"/api/trips/{trip_id}/cancellations",
        json={"scope": "rest", "trip_day": 1, "cause": "weather"},
    )

    assert replayed.status_code == 201, replayed.text
    # one ask per trip day the warning is in force on: about 12 hours from now, so day 2 too
    # when the replay is close to Manila midnight
    assert asks
    assert {p["kind"] for p in asks} == {"warning"}
    assert cancelled.status_code == 201, cancelled.text
    proposal = cancelled.json()
    assert proposal["state"] == "asked"  # two remedies: the operator chooses
    for purpose in ("deposit", "balance"):
        labels = [option["label"] for option in proposal["parts"][purpose]]
        assert labels[:2] == ["refund 100%", "credit 100%"]
    view = (await client.get(f"/api/guest/{seeded['guests'][0]['token']}/settlement")).json()
    assert view["quoted_from"]["name"] == "Pure Dive Resort"
