"""Integration tests run against a throwaway PostgreSQL 18 container, migrated once per session."""

from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from testcontainers.community.postgres import PostgresContainer

from tests.integration.fake_forecast import FakeForecast
from tests.integration.fake_model import FakeModel
from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import POLICY
from tidehold.bootstrap.app import create_app
from tidehold.platform.db import Conn, connect
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import Settings


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:18-alpine", driver=None) as pg:
        url = pg.get_connection_url()
        migrate(url)
        yield url


ORIGIN = "http://test"


def _truncate(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "truncate due_actions, credits, payment_waivers, money_commands, proposals,"
            " cancellations, guests, payment_events, payments, trips, policy_versions, model_calls,"
            " warning_replays, official_warnings, corroborations, workspaces"
        )


@pytest.fixture
def paypal() -> FakePayPal:
    return FakePayPal()


@pytest.fixture
def forecast() -> FakeForecast:
    return FakeForecast()


@pytest.fixture
def model() -> FakeModel:
    """The compiler's model; it compiles `POLICY`'s wording into `POLICY`'s rules."""
    fake = FakeModel()
    fake.answer(POLICY["wording"], POLICY["rules"], [])
    return fake


@pytest.fixture
def daily_cap() -> int:
    """Live model calls per day; a test lowers it with `@pytest.mark.parametrize`."""
    return 50


@pytest.fixture
def app(
    database_url: str,
    paypal: FakePayPal,
    model: FakeModel,
    forecast: FakeForecast,
    daily_cap: int,
) -> Iterator[FastAPI]:
    settings = Settings(
        database_url=database_url, public_origin=ORIGIN, model_daily_live_cap=daily_cap
    )
    yield create_app(settings, paypal=paypal.client(), compiler=model, forecasts=forecast.client())
    _truncate(database_url)


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[Conn]:
    """An application connection (autocommit), for module-level tests below HTTP."""
    async with await connect(database_url) as c:
        yield c
    _truncate(database_url)


def _client(app: FastAPI) -> AsyncClient:
    """A same-origin browser: it sends the Origin header the HTTP boundary expects."""
    return AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A browser with no session: a guest, or an operator before the workspace exists."""
    async with _client(app) as c:
        yield c


async def _operator(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(app) as c:
        response = await c.post("/api/workspaces")
        assert response.status_code == 201
        yield c


@pytest.fixture
async def operator(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A browser whose cookie jar holds a fresh workspace session."""
    async for c in _operator(app):
        yield c


@pytest.fixture
async def other_operator(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async for c in _operator(app):
        yield c
