from httpx import AsyncClient

from tidehold.platform.migrate import migrate

TRIP = {"name": "Anilao weekend", "starts_on": "2026-12-05", "days": 3}


async def test_a_guest_link_shows_the_trip_the_operator_created(
    operator: AsyncClient, client: AsyncClient
) -> None:
    created = (await operator.post("/api/trips", json=TRIP)).json()

    guest = await client.get(f"/api/guest/{created['guest_token']}")

    assert guest.status_code == 200
    assert guest.json() == {
        "name": "Anilao weekend",
        "starts_on": "2026-12-05",
        "ends_on": "2026-12-07",
        "days": 3,
    }


async def test_an_unknown_guest_token_is_not_found(
    operator: AsyncClient, client: AsyncClient
) -> None:
    await operator.post("/api/trips", json=TRIP)

    response = await client.get("/api/guest/not-a-real-token")

    assert response.status_code == 404


async def test_creating_a_trip_needs_a_workspace_session(client: AsyncClient) -> None:
    response = await client.post("/api/trips", json=TRIP)

    assert response.status_code == 401


async def test_a_trip_longer_than_ten_days_is_refused(operator: AsyncClient) -> None:
    response = await operator.post("/api/trips", json={**TRIP, "days": 11})

    assert response.status_code == 422


async def test_workspaces_see_only_their_own_trips(
    operator: AsyncClient, other_operator: AsyncClient
) -> None:
    await operator.post("/api/trips", json=TRIP)

    mine = (await operator.get("/api/trips")).json()
    theirs = (await other_operator.get("/api/trips")).json()

    assert [t["name"] for t in mine] == ["Anilao weekend"]
    assert theirs == []


def test_migrations_apply_once(database_url: str) -> None:
    assert migrate(database_url) == []
