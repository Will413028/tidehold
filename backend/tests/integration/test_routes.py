from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient

import tidehold
from tidehold.bootstrap.app import create_app
from tidehold.bootstrap.routes import Access
from tidehold.platform.settings import Settings

TRIP = {"name": "Anilao weekend", "starts_on": "2026-12-05", "days": 3}


def _operations(app: FastAPI) -> list[tuple[str, str, list[str], list[str]]]:
    """(method, path, tags, path parameters) of every route, read from the OpenAPI schema,
    the public view of routes inside included routers."""
    app.openapi_schema = None
    return [
        (
            method.upper(),
            path,
            op.get("tags", []),
            [p["name"] for p in op.get("parameters", []) if p["in"] == "path"],
        )
        for path, ops in app.openapi()["paths"].items()
        for method, op in ops.items()
    ]


def unclassified(app: FastAPI) -> list[str]:
    """Routes not mounted through exactly one access class of the route table."""
    classes = {a.value for a in Access}
    return [path for _, path, tags, _ in _operations(app) if len(classes.intersection(tags)) != 1]


def _app() -> FastAPI:
    return create_app(Settings(database_url="postgresql://unused", public_origin="http://test"))


def _id_bearing_workspace_routes() -> list[tuple[str, str, tuple[str, ...]]]:
    return sorted(
        (method, path, tuple(params))
        for method, path, tags, params in _operations(_app())
        if Access.WORKSPACE in tags and params
    )


ROUTES = _id_bearing_workspace_routes()


async def _trip(operator: AsyncClient) -> str:
    trip_id: str = (await operator.post("/api/trips", json=TRIP)).json()["id"]
    return trip_id


# How workspace A makes the thing each path parameter names. A new ID-bearing route with a
# parameter missing here fails the cross-workspace test until its factory is added.
ID_FACTORIES: dict[str, Callable[[AsyncClient], Awaitable[str]]] = {"trip_id": _trip}


def test_every_route_is_classified() -> None:
    assert unclassified(_app()) == []


def test_a_route_mounted_outside_the_table_is_reported() -> None:
    app = _app()
    stray = APIRouter()

    @stray.get("/api/stray/{thing_id}")
    async def stray_route(thing_id: str) -> str:
        return thing_id

    app.include_router(stray)

    assert unclassified(app) == ["/api/stray/{thing_id}"]


def test_the_cross_workspace_check_covers_some_routes() -> None:
    assert ROUTES


def test_no_route_hides_from_the_schema_the_table_check_reads() -> None:
    sources = Path(tidehold.__file__).parent.rglob("*.py")
    assert [p.name for p in sources if "include_in_schema" in p.read_text()] == []


@pytest.mark.parametrize(("method", "path", "params"), ROUTES)
async def test_another_workspaces_ids_are_not_found(
    method: str,
    path: str,
    params: tuple[str, ...],
    operator: AsyncClient,
    other_operator: AsyncClient,
) -> None:
    ids = {name: await ID_FACTORIES[name](operator) for name in params}

    response = await other_operator.request(method, path.format(**ids), json={})

    assert response.status_code == 404, response.text
