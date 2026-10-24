import psycopg
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.integration.conftest import ORIGIN
from tidehold.identity.sessions import address_key

CAP = 20  # Settings.workspace_daily_cap_per_address


async def _create_from(app: FastAPI, address: str) -> int:
    """One new browser at `address` creates its workspace (the address uvicorn's proxy-headers
    middleware resolved, as the ASGI scope's client)."""
    transport = ASGITransport(app=app, client=(address, 40000))
    async with AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
        return (await c.post("/api/workspaces")).status_code


async def test_an_expired_session_is_refused(operator: AsyncClient, database_url: str) -> None:
    workspace = (await operator.get("/api/workspaces/me")).json()["id"]
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "update workspaces set session_expires_at = now() - interval '1 second' where id = %s",
            (workspace,),
        )

    response = await operator.get("/api/workspaces/me")

    assert response.status_code == 401


async def test_a_session_is_valid_until_it_expires(operator: AsyncClient) -> None:
    response = await operator.get("/api/workspaces/me")

    assert response.status_code == 200


async def test_a_left_workspace_refuses_its_old_cookie(operator: AsyncClient) -> None:
    cookie = operator.cookies["tidehold_session"]

    left = await operator.post("/api/workspaces/leave")
    operator.cookies.set("tidehold_session", cookie)
    after = await operator.get("/api/workspaces/me")

    assert left.status_code == 204
    assert after.status_code == 401


async def test_workspace_creation_is_capped_per_address_per_day(app: FastAPI) -> None:
    created = [await _create_from(app, "203.0.113.7") for _ in range(CAP)]

    refused = await _create_from(app, "203.0.113.7")
    elsewhere = await _create_from(app, "198.51.100.9")

    assert created == [201] * CAP
    assert refused == 429
    assert elsewhere == 201


async def test_one_ipv6_64_shares_the_cap(app: FastAPI) -> None:
    for i in range(CAP):
        assert await _create_from(app, f"2001:db8:1:2::{i + 1:x}") == 201

    assert await _create_from(app, "2001:db8:1:2:ffff::1") == 429
    assert await _create_from(app, "2001:db8:1:3::1") == 201


def test_the_address_key() -> None:
    assert address_key("203.0.113.7") == "203.0.113.7"
    assert address_key("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert address_key("::ffff:203.0.113.7") == "203.0.113.7"
    assert address_key("testclient") == "unknown"
    assert address_key(None) == "unknown"
