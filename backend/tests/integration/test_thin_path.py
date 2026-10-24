from httpx import AsyncClient

from tests.integration.payloads import TRIP, post_trip
from tidehold.platform.migrate import migrate


async def _guest_link(operator: AsyncClient) -> str:
    trip = (await post_trip(operator)).json()
    invited = (await operator.post(f"/api/trips/{trip['id']}/guests", json={"name": "Ana"})).json()
    token: str = invited["guests"][0]["token"]
    return token


async def test_a_guest_link_shows_the_guests_trip(
    operator: AsyncClient, client: AsyncClient
) -> None:
    token = await _guest_link(operator)

    guest = await client.get(f"/api/guest/{token}")

    assert guest.status_code == 200
    body = guest.json()
    assert (body["guest_name"], body["trip_name"]) == ("Ana", "Anilao weekend")
    assert (body["starts_on"], body["ends_on"], body["days"]) == ("2026-12-05", "2026-12-07", 3)
    assert body["deposit"] == {
        "state": "pending",
        "amount_minor": 5000,
        "due_minor": 5000,
        "captured_minor": 0,
        "refunded_minor": 0,
    }
    assert body["balance"]["payment"] == {
        "state": "pending",
        "amount_minor": 30000,
        "due_minor": 30000,
        "captured_minor": 0,
        "refunded_minor": 0,
    }
    assert body["released"] is False
    assert "workspace" not in str(body)
    assert "id" not in body


async def test_an_unknown_guest_token_is_not_found(
    operator: AsyncClient, client: AsyncClient
) -> None:
    await _guest_link(operator)

    response = await client.get("/api/guest/not-a-real-token")

    assert response.status_code == 404


async def test_creating_a_trip_needs_a_workspace_session(client: AsyncClient) -> None:
    response = await client.post("/api/trips", json=TRIP)

    assert response.status_code == 401


async def test_a_trip_of_the_longest_allowed_length_is_accepted_and_one_longer_is_refused(
    operator: AsyncClient,
) -> None:
    longest = await post_trip(operator, days=21)
    cancelled = await operator.post(
        f"/api/trips/{longest.json()['id']}/cancellations",
        json={"scope": "day", "trip_day": 21, "cause": "weather"},
    )
    longer = await post_trip(operator, days=22)

    assert (longest.status_code, longest.json()["ends_on"]) == (201, "2026-12-25")
    assert cancelled.status_code == 201, cancelled.text
    assert (longer.status_code, longer.json()) == (400, {"detail": "INVALID_REQUEST"})


async def test_workspaces_see_only_their_own_trips(
    operator: AsyncClient, other_operator: AsyncClient
) -> None:
    await post_trip(operator)

    mine = (await operator.get("/api/trips")).json()
    theirs = (await other_operator.get("/api/trips")).json()

    assert [t["name"] for t in mine] == ["Anilao weekend"]
    assert theirs == []


def test_migrations_apply_once(database_url: str) -> None:
    assert migrate(database_url) == []
