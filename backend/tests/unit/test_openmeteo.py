from datetime import UTC, date, datetime
from decimal import Decimal

import httpx2 as httpx
import pytest

from tidehold.platform.errors import Unavailable
from tidehold.warnings import openmeteo

NOW = datetime(2026, 12, 4, tzinfo=UTC)
DAYS = (date(2026, 12, 5), date(2026, 12, 6))


def _client(answer: httpx.Response, seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_the_daily_maximum_wave_height_at_the_site_is_read() -> None:
    seen: list[httpx.Request] = []
    body = {"daily": {"time": ["2026-12-05", "2026-12-06"], "wave_height_max": [1.24, None]}}
    async with _client(httpx.Response(200, json=body), seen) as client:
        rows = await openmeteo.fetch(client, lat=13.7565, lon=120.9137, days=DAYS, now=NOW)

    assert [(r.day, r.max_wave_height_m) for r in rows] == [
        (date(2026, 12, 5), Decimal("1.24")),
        (date(2026, 12, 6), None),
    ]
    assert seen[0].url.host == "marine-api.open-meteo.com"
    assert seen[0].url.params["daily"] == "wave_height_max"


@pytest.mark.parametrize("answer", [httpx.Response(502), httpx.Response(200, json={"error": 1})])
async def test_a_failed_forecast_is_unavailable_not_a_trigger(answer: httpx.Response) -> None:
    async with _client(answer, []) as client:
        with pytest.raises(Unavailable, match="OPEN_METEO_UNAVAILABLE"):
            await openmeteo.fetch(client, lat=13.7, lon=120.9, days=DAYS, now=NOW)
