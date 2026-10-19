from httpx import AsyncClient

TRIP = {"name": "Anilao weekend", "starts_on": "2026-12-05", "days": 3}


async def test_a_post_from_another_origin_is_refused(operator: AsyncClient) -> None:
    response = await operator.post(
        "/api/trips", json=TRIP, headers={"Origin": "https://evil.example"}
    )

    assert (response.status_code, response.json()) == (400, {"detail": "INVALID_ORIGIN"})


async def test_a_post_without_an_origin_is_refused(operator: AsyncClient) -> None:
    del operator.headers["Origin"]

    response = await operator.post("/api/trips", json=TRIP)

    assert response.json() == {"detail": "INVALID_ORIGIN"}


async def test_a_form_body_is_refused(operator: AsyncClient) -> None:
    response = await operator.post("/api/trips", data={"name": "x"})

    assert response.json() == {"detail": "INVALID_CONTENT_TYPE"}


async def test_a_body_over_32_kib_is_refused(operator: AsyncClient) -> None:
    response = await operator.post("/api/trips", json={**TRIP, "name": "x" * 40_000})

    assert response.json() == {"detail": "BODY_TOO_LARGE"}


async def test_other_methods_are_refused(operator: AsyncClient) -> None:
    response = await operator.put("/api/trips", json=TRIP)

    assert response.status_code == 405


async def test_responses_are_not_cached_or_sniffed(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
