"""Anonymous operator workspaces: a browser gets one workspace, identified by a session cookie."""

import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tidehold.platform.db import Conn, connection, insert_returning

COOKIE = "tidehold_session"
# The cookie's lifetime and the server-side expiry stored with the session.
SESSION_SECONDS = 60 * 60 * 24 * 30

# Creating a workspace needs no session; everything else on it does.
public_router = APIRouter(prefix="/api/workspaces")
router = APIRouter(prefix="/api/workspaces")


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def current_workspace(request: Request, conn: Annotated[Conn, Depends(connection)]) -> UUID:
    token = request.cookies.get(COOKIE)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no workspace session")
    row = await (
        await conn.execute(
            "select id from workspaces"
            " where session_token_hash = %s and session_expires_at > now()",
            (_hash(token),),
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown workspace session")
    workspace_id: UUID = row["id"]
    return workspace_id


async def new_workspace(conn: Conn) -> tuple[UUID, str]:
    """A workspace and the session token for its cookie; only the token's hash is stored."""
    token = secrets.token_urlsafe(32)
    row = await insert_returning(
        conn,
        "insert into workspaces (session_token_hash, session_expires_at)"
        " values (%s, now() + make_interval(secs => %s)) returning id",
        (_hash(token), SESSION_SECONDS),
    )
    workspace_id: UUID = row["id"]
    return workspace_id, token


@public_router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: Request, response: Response, conn: Annotated[Conn, Depends(connection)]
) -> dict[str, str]:
    workspace_id, token = await new_workspace(conn)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.cookie_secure,
        max_age=SESSION_SECONDS,
    )
    return {"id": str(workspace_id)}


@router.post("/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_workspace(
    response: Response,
    workspace_id: Annotated[UUID, Depends(current_workspace)],
    conn: Annotated[Conn, Depends(connection)],
) -> None:
    """Forget this browser's session: the stored hash is cleared, so the cookie is dead
    even if a copy of it survives elsewhere."""
    await conn.execute(
        "update workspaces set session_token_hash = null where id = %s", (workspace_id,)
    )
    response.delete_cookie(COOKIE)


@router.get("/me")
async def me(workspace_id: Annotated[UUID, Depends(current_workspace)]) -> dict[str, str]:
    return {"id": str(workspace_id)}
