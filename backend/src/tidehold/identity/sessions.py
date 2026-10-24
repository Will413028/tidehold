"""Anonymous operator workspaces: a browser gets one workspace, identified by a session cookie."""

import hashlib
import ipaddress
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from tidehold.platform.db import Conn, connection, insert_returning
from tidehold.platform.errors import LimitReached

COOKIE = "tidehold_session"
# The cookie's lifetime and the server-side expiry stored with the session.
SESSION_SECONDS = 60 * 60 * 24 * 30

# Creating a workspace needs no session; everything else on it does.
public_router = APIRouter(prefix="/api/workspaces")
router = APIRouter(prefix="/api/workspaces")


def address_key(host: str | None) -> str:
    """Whom the workspace-creation cap counts: an IPv4 address, or an IPv6 address's /64 (one
    host usually controls the whole /64). Anything else shares the one bucket `unknown`, so an
    unparsable address cannot escape the cap."""
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return "unknown"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network((address, 64), strict=False))
    return str(address)


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


async def _within_creation_cap(conn: Conn, key: str, cap: int) -> None:
    """Count today's creations from this address under a lock on the address, so two
    concurrent creations cannot both take the last slot, then record this one."""
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
    row = await (
        await conn.execute(
            "select count(*) as n from workspace_creations where address_key = %s"
            " and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'",
            (key,),
        )
    ).fetchone()
    if row is not None and row["n"] >= cap:
        raise LimitReached("WORKSPACE_CREATION_CAP_REACHED")
    await conn.execute("insert into workspace_creations (address_key) values (%s)", (key,))


@public_router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: Request, response: Response, conn: Annotated[Conn, Depends(connection)]
) -> dict[str, str]:
    # The client host is the forwarded address: uvicorn's proxy-headers middleware trusts
    # X-Forwarded-For from loopback only, where web proxies /api (step10a-design.md).
    key = address_key(request.client.host if request.client else None)
    async with conn.transaction():
        await _within_creation_cap(
            conn, key, request.app.state.settings.workspace_daily_cap_per_address
        )
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
