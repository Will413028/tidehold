import psycopg
from httpx import AsyncClient


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
