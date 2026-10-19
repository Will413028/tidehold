from collections.abc import Awaitable, Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient

from tests.integration.payloads import post_trip, publish_policy
from tidehold.bootstrap.app import create_app
from tidehold.bootstrap.routes import Access
from tidehold.platform.settings import Settings


def _operations(app: FastAPI) -> list[tuple[str, str, list[str], list[str], set[str]]]:
    """(method, path, tags, path parameters, dependency names) of every API route, through
    FastAPI's public walker: included routers are lazy since 0.137, so `app.routes` alone
    misses them (second-brain wiki/tech/fastapi-included-router-route-walking)."""
    return [
        (
            method,
            str(context.path),
            [str(t) for t in context.tags],
            [p.name for p in context.dependant.path_params],
            _dependency_names(context.dependant),
        )
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        for method in sorted(context.methods or ())
    ]


def _dependency_names(dependant: Dependant) -> set[str]:
    names: set[str] = set()
    for sub in dependant.dependencies:
        names.add(getattr(sub.call, "__name__", ""))
        names |= _dependency_names(sub)
    return names


def unclassified(app: FastAPI) -> list[str]:
    """Routes not mounted through exactly one access class of the route table."""
    classes = {a.value for a in Access}
    return [
        path for _, path, tags, _, _ in _operations(app) if len(classes.intersection(tags)) != 1
    ]


def _app() -> FastAPI:
    return create_app(Settings(database_url="postgresql://unused", public_origin="http://test"))


def _id_bearing_workspace_routes() -> list[tuple[str, str, tuple[str, ...]]]:
    return sorted(
        (method, path, tuple(params))
        for method, path, tags, params, _ in _operations(_app())
        if Access.WORKSPACE in tags and params
    )


ROUTES = _id_bearing_workspace_routes()


async def _trip(operator: AsyncClient) -> str:
    trip_id: str = (await post_trip(operator)).json()["id"]
    return trip_id


# How workspace A makes the thing each path parameter names. A new ID-bearing route with a
# parameter missing here fails the cross-workspace test until its factory is added.
ID_FACTORIES: dict[str, Callable[[AsyncClient], Awaitable[str]]] = {
    "trip_id": _trip,
    "version_id": publish_policy,
}
# A valid body, so a route that validates its body still answers 404, not 400.
BODIES: dict[tuple[str, str], dict[str, object]] = {
    ("POST", "/api/trips/{trip_id}/guests"): {"name": "Ana"},
}


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


def test_exactly_the_workspace_routes_check_the_session() -> None:
    checked = {
        (method, path): "current_workspace" in deps
        for method, path, tags, _, deps in _operations(_app())
    }
    workspace = {
        (method, path)
        for method, path, tags, _, _ in _operations(_app())
        if Access.WORKSPACE in tags
    }

    assert workspace
    assert {route for route, has in checked.items() if has} == workspace


@pytest.mark.parametrize(("method", "path", "params"), ROUTES)
async def test_another_workspaces_ids_are_not_found(
    method: str,
    path: str,
    params: tuple[str, ...],
    operator: AsyncClient,
    other_operator: AsyncClient,
) -> None:
    ids = {name: await ID_FACTORIES[name](operator) for name in params}

    response = await other_operator.request(
        method, path.format(**ids), json=BODIES.get((method, path), {})
    )

    assert response.status_code == 404, response.text
