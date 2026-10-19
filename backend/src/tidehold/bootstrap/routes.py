"""The route table: every API route is mounted through exactly one access class.

Workspace routers are mounted behind the session dependency, so a route in them cannot
forget the check; `tests/integration/test_routes.py` fails on a route mounted any other
way and sends one workspace's IDs from another to every ID-bearing workspace route.
"""

from enum import StrEnum

from fastapi import APIRouter, Depends, FastAPI

from tidehold.holds import http as holds
from tidehold.identity import sessions
from tidehold.trips import http as trips


class Access(StrEnum):
    WORKSPACE = "workspace"  # needs the operator's session; IDs of other workspaces are 404
    GUEST = "guest"  # reachable by an unguessable guest token
    PUBLIC = "public"  # no session: health, creating a workspace


health = APIRouter(prefix="/api")


@health.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


TABLE: dict[Access, list[APIRouter]] = {
    Access.WORKSPACE: [sessions.router, trips.router],
    Access.GUEST: [holds.router],
    Access.PUBLIC: [health, sessions.public_router, holds.public_router],
}


def mount(app: FastAPI) -> None:
    for access, routers in TABLE.items():
        dependencies = [Depends(sessions.current_workspace)] if access is Access.WORKSPACE else []
        for router in routers:
            app.include_router(router, tags=[access.value], dependencies=dependencies)
