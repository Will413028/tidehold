"""Open-Meteo's marine API, in memory: no test reaches the network."""

from datetime import date, timedelta

import httpx2


class FakeForecast:
    """Open-Meteo's marine API: every requested day gets a wave height, unless `fail`."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.fail = False

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.fail:
            return httpx2.Response(502)
        start = date.fromisoformat(request.url.params["start_date"])
        end = date.fromisoformat(request.url.params["end_date"])
        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
        daily = {"time": [d.isoformat() for d in days], "wave_height_max": [1.5] * len(days)}
        return httpx2.Response(200, json={"daily": daily})
