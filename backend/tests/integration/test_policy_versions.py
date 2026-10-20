"""Policy versions written by hand and bound by trips (plan step 5 acceptance)."""

from typing import Any

import psycopg
import pytest
from httpx import AsyncClient

from tests.integration.payloads import POLICY, TRIP, post_trip, publish_policy
from tidehold.platform.db import Conn

RSQUO = chr(0x2019)  # typographic apostrophe
WARNED = (
    "Liveaboards: we refund all monies paid when we cancel a trip because the weather service "
    "issued a gale warning or stronger for our area. Day trips: if we cancel a day trip for "
    "weather, you receive credit for another day."
)


def _rule(quote: str, **fields: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "product": None,
        "causes": ["weather"],
        "warning": None,
        "scope": "per_trip",
        "applies_to": "both",
        "remedies": [
            {"kind": "refund", "percent": 100, "deductions": [], "credit_expiry_months": None}
        ],
        "chooser": None,
        "quote": quote,
    }
    return {**rule, **fields}


def _warning(source: str | None, values: list[str]) -> dict[str, Any]:
    return {
        "named_as": "the weather service",
        "source": source,
        "event_values": values,
        "quote": "the weather service issued a gale warning or stronger for our area",
    }


def _warned(source: str | None = "nws", values: list[str] | None = None) -> dict[str, Any]:
    values = ["Gale Warning", "Storm Warning"] if values is None else values
    return {
        "wording": WARNED,
        "rules": [
            _rule(
                "we refund all monies paid when we cancel a trip because the weather service "
                "issued a gale warning or stronger for our area",
                warning=_warning(source, values),
            )
        ],
    }


async def test_workspaces_are_isolated_and_policy_is_immutable(
    operator: AsyncClient, other_operator: AsyncClient, conn: Conn
) -> None:
    version_id = await publish_policy(operator)
    trip_id = (await post_trip(operator, policy_version_id=version_id)).json()["id"]
    other_version = await publish_policy(other_operator)

    assert (await other_operator.get(f"/api/policy-versions/{version_id}")).status_code == 404
    assert [v["id"] for v in (await other_operator.get("/api/policy-versions")).json()] == [
        other_version
    ]
    with pytest.raises(psycopg.errors.RaiseException, match="POLICY_VERSION_IMMUTABLE"):
        await conn.execute("update policy_versions set rules = '[]' where id = %s", (version_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="POLICY_VERSION_IMMUTABLE"):
        await conn.execute("delete from policy_versions where id = %s", (version_id,))
    second = await publish_policy(operator)
    with pytest.raises(psycopg.errors.RaiseException, match="TRIP_POLICY_BOUND"):
        await conn.execute(
            "update trips set policy_version_id = %s where id = %s", (second, trip_id)
        )


async def test_a_trip_cannot_be_created_without_a_policy_version(
    operator: AsyncClient, other_operator: AsyncClient
) -> None:
    missing = await operator.post("/api/trips", json=TRIP)
    theirs = await post_trip(operator, policy_version_id=await publish_policy(other_operator))

    assert (missing.status_code, missing.json()) == (400, {"detail": "INVALID_REQUEST"})
    assert theirs.status_code == 404
    assert (await operator.get("/api/trips")).json() == []


async def test_a_version_shows_each_clause_cut_from_the_stored_wording(
    operator: AsyncClient,
) -> None:
    wording = f"If we cancel a dive because of the weather, you{RSQUO}ll get a full refund."
    policy = {"wording": wording, "rules": [_rule("you'll get a full refund")]}

    version = (await operator.post("/api/policy-versions", json=policy)).json()

    assert version["rules"][0]["clause"] == f"you{RSQUO}ll get a full refund"
    assert version["rules"][0]["rule"]["quote"] == "you'll get a full refund"
    assert version["warning_source"] is None


async def test_a_fabricated_span_is_rejected(operator: AsyncClient) -> None:
    fabricated = {**POLICY, "rules": [_rule("We refund 100% whenever a guest asks.")]}

    response = await operator.post("/api/policy-versions", json=fabricated)

    assert (response.status_code, response.json()) == (400, {"detail": "SPAN_NOT_FOUND"})
    assert (await operator.get("/api/policy-versions")).json() == []


async def test_a_warning_condition_quote_must_be_found_too(operator: AsyncClient) -> None:
    policy = _warned()
    policy["rules"][0]["warning"]["quote"] = "an official typhoon signal"

    response = await operator.post("/api/policy-versions", json=policy)

    assert response.json() == {"detail": "SPAN_NOT_FOUND"}


@pytest.mark.parametrize(
    ("source", "values", "code"),
    [
        ("nws", ["Gale Warning", "Tornado Warning"], "EVENT_VALUE_NOT_IN_CATALOGUE"),
        ("nws", ["Wind:orange"], "EVENT_VALUE_NOT_IN_CATALOGUE"),
        ("nws", [], "EVENT_SET_EMPTY"),
        (None, ["Gale Warning"], "WARNING_SOURCE_MISSING"),
        ("jma", ["Gale Warning"], "WARNING_SOURCE_NOT_IN_CATALOGUE"),
    ],
)
async def test_an_event_set_outside_the_sources_catalogue_is_refused(
    source: str | None, values: list[str], code: str, operator: AsyncClient
) -> None:
    response = await operator.post("/api/policy-versions", json=_warned(source, values))

    assert (response.status_code, response.json()) == (400, {"detail": code})


async def test_a_confirmed_event_set_is_stored_with_its_source(operator: AsyncClient) -> None:
    version = (await operator.post("/api/policy-versions", json=_warned())).json()

    assert version["warning_source"] == "nws"
    assert version["rules"][0]["rule"]["warning"]["event_values"] == [
        "Gale Warning",
        "Storm Warning",
    ]
    assert version["rules"][0]["warning_clause"] == (
        "the weather service issued a gale warning or stronger for our area"
    )


async def test_the_trips_warning_source_must_be_the_one_its_policy_names(
    operator: AsyncClient,
) -> None:
    version_id = await publish_policy(operator, _warned())

    pagasa = await post_trip(operator, policy_version_id=version_id)
    nws = await post_trip(
        operator, policy_version_id=version_id, warning_source="nws", warning_areas=["PZZ530"]
    )

    assert (pagasa.status_code, pagasa.json()) == (400, {"detail": "WARNING_SOURCE_MISMATCH"})
    assert nws.status_code == 201
    assert nws.json()["policy_version_id"] == version_id


async def test_one_policy_names_one_warning_source(operator: AsyncClient) -> None:
    policy = _warned()
    second = _rule(
        "if we cancel a day trip for weather, you receive credit for another day",
        warning=_warning("meteoalarm", ["Wind:orange"]),
    )
    policy["rules"].append(second)

    response = await operator.post("/api/policy-versions", json=policy)

    assert response.json() == {"detail": "MIXED_WARNING_SOURCES"}


async def test_two_remedies_need_to_say_who_chooses(operator: AsyncClient) -> None:
    credit = {"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": 12}
    rule = POLICY["rules"][0]
    unclear = {**POLICY, "rules": [{**rule, "remedies": [*rule["remedies"], credit]}]}
    chosen = {**POLICY, "rules": [{**unclear["rules"][0], "chooser": "guest"}]}

    refused = await operator.post("/api/policy-versions", json=unclear)
    accepted = await operator.post("/api/policy-versions", json=chosen)

    assert refused.json() == {"detail": "CHOOSER_MISSING"}
    assert accepted.status_code == 201


async def test_a_version_with_several_products_needs_the_trips_product(
    operator: AsyncClient,
) -> None:
    liveaboard = _rule("we refund all monies paid when we cancel a trip", product="Liveaboards:")
    day_trip = _rule(
        "if we cancel a day trip for weather, you receive credit for another day",
        product="Day trips:",
        remedies=[{"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": 6}],
    )
    version_id = await publish_policy(
        operator, {"wording": WARNED, "rules": [liveaboard, day_trip]}
    )

    unnamed = await post_trip(operator, policy_version_id=version_id)
    unknown = await post_trip(operator, policy_version_id=version_id, policy_product="Courses")
    named = await post_trip(operator, policy_version_id=version_id, policy_product="Day trips:")

    invented = await operator.post(
        "/api/policy-versions",
        json={"wording": WARNED, "rules": [{**liveaboard, "product": "Safaris:"}]},
    )

    assert invented.json() == {"detail": "PRODUCT_NOT_FOUND"}
    assert unnamed.json() == {"detail": "POLICY_PRODUCT_REQUIRED"}
    assert unknown.json() == {"detail": "POLICY_PRODUCT_UNKNOWN"}
    assert named.json()["policy_product"] == "Day trips:"


async def test_rulings_without_a_compilation_are_refused(operator: AsyncClient) -> None:
    response = await operator.post(
        "/api/policy-versions", json={**POLICY, "rulings": [{"index": 0, "answer": "Ask me."}]}
    )

    assert response.json() == {"detail": "RULING_UNKNOWN"}


async def test_a_policy_with_no_rules_is_a_valid_version(operator: AsyncClient) -> None:
    """Wording with no weather clause: every cancellation becomes an operator ask (step 8)."""
    response = await operator.post("/api/policy-versions", json={**POLICY, "rules": []})

    assert response.status_code == 201
    assert response.json()["rules"] == []
