"""Open-Meteo marine forecast at the dive site: corroboration only (plan step 7).

A forecast never settles or conditions anything (D16). Open-Meteo says its marine data "is not suitable
for coastal navigation"; its data is attributed to DWD and Open-Meteo wherever it is shown
(marine weather API docs, read 2026-10-19). Free non-commercial use, no key.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx2 as httpx

from tidehold.platform.db import Conn
from tidehold.platform.errors import Unavailable

MARINE_API = "https://marine-api.open-meteo.com/v1/marine"
ATTRIBUTION = "Marine forecast: Open-Meteo, from DWD ICON Wave; not for coastal navigation"


@dataclass(frozen=True)
class Corroboration:
    site_lat: float
    site_lon: float
    day: date
    max_wave_height_m: Decimal | None
    url: str
    fetched_at: datetime


async def fetch(
    client: httpx.AsyncClient, *, lat: float, lon: float, days: tuple[date, date], now: datetime
) -> list[Corroboration]:
    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "daily": "wave_height_max",
        "timezone": "UTC",
        "start_date": days[0].isoformat(),
        "end_date": days[1].isoformat(),
    }
    try:
        response = await client.get(MARINE_API, params=params, timeout=15.0)
        response.raise_for_status()
        daily = response.json()["daily"]
        rows = zip(daily["time"], daily["wave_height_max"], strict=True)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise Unavailable("OPEN_METEO_UNAVAILABLE") from error
    return [
        Corroboration(
            site_lat=lat,
            site_lon=lon,
            day=date.fromisoformat(day),
            max_wave_height_m=None if height is None else Decimal(str(height)),
            url=str(response.url),
            fetched_at=now,
        )
        for day, height in rows
    ]


async def store(conn: Conn, corroborations: list[Corroboration]) -> None:
    for c in corroborations:
        await conn.execute(
            "insert into corroborations (site_lat, site_lon, day, max_wave_height_m, url,"
            " fetched_at) values (%s, %s, %s, %s, %s, %s)",
            (c.site_lat, c.site_lon, c.day, c.max_wave_height_m, c.url, c.fetched_at),
        )
