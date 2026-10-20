"""Open-Meteo marine forecast at the dive site: corroboration only (plan step 7).

A forecast never settles or conditions anything (D16). Open-Meteo says its marine data "is not
suitable for coastal navigation"; its data is attributed to DWD and Open-Meteo wherever it is shown
(marine weather API docs, read 2026-10-19). Free non-commercial use, no key.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    timezone: str
    day: date
    max_wave_height_m: Decimal | None
    url: str
    fetched_at: datetime


@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    # an IANA name: the daily maximum is per local day of the trip
    timezone: str


async def fetch(
    client: httpx.AsyncClient, site: Site, days: tuple[date, date], now: datetime
) -> list[Corroboration]:
    params = {
        "latitude": f"{site.lat:.5f}",
        "longitude": f"{site.lon:.5f}",
        "daily": "wave_height_max",
        "timezone": site.timezone,
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
            site_lat=site.lat,
            site_lon=site.lon,
            timezone=site.timezone,
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
            "insert into corroborations (site_lat, site_lon, timezone, day, max_wave_height_m,"
            " url, fetched_at) values (%s, %s, %s, %s, %s, %s, %s)",
            (c.site_lat, c.site_lon, c.timezone, c.day, c.max_wave_height_m, c.url, c.fetched_at),
        )


# A page reload reuses a fetch this recent instead of calling the API again.
REUSE = timedelta(hours=1)


async def _recent(conn: Conn, site: Site, now: datetime) -> list[Corroboration]:
    """The site's newest fetch made within `REUSE` of `now`, all of its days."""
    rows = await (
        await conn.execute(
            "select * from corroborations where (site_lat, site_lon, timezone, fetched_at) in"
            " (select site_lat, site_lon, timezone, max(fetched_at) from corroborations"
            "  where site_lat = round(%(lat)s::numeric, 5)"
            "  and site_lon = round(%(lon)s::numeric, 5)"
            "  and timezone = %(tz)s and fetched_at > %(since)s"
            "  group by site_lat, site_lon, timezone)"
            " order by day",
            {"lat": site.lat, "lon": site.lon, "tz": site.timezone, "since": now - REUSE},
        )
    ).fetchall()
    return [
        Corroboration(
            site_lat=float(r["site_lat"]),
            site_lon=float(r["site_lon"]),
            timezone=r["timezone"],
            day=r["day"],
            max_wave_height_m=r["max_wave_height_m"],
            url=r["url"],
            fetched_at=r["fetched_at"],
        )
        for r in rows
    ]


async def forecast(
    conn: Conn, client: httpx.AsyncClient, site: Site, days: tuple[date, date], now: datetime
) -> list[Corroboration]:
    """The forecast for `days` at `site`: reused within the hour, else fetched and stored.
    Raises `Unavailable` when the API fails."""
    recent = [c for c in await _recent(conn, site, now) if days[0] <= c.day <= days[1]]
    if len({c.day for c in recent}) == (days[1] - days[0]).days + 1:
        return recent
    fetched = await fetch(client, site, days, now)
    await store(conn, fetched)
    # read back, so a fresh fetch and a reused one look the same (numeric(5, 2))
    return [c for c in await _recent(conn, site, now) if days[0] <= c.day <= days[1]]
