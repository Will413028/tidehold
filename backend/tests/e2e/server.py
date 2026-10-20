"""The backend the browser tests (web/e2e) run against: the real app on a throwaway
PostgreSQL 18 holding the committed recorded feeds, with PayPal, the model and Open-Meteo
replaced by the integration tests' fakes and the due-action worker on. It lives under tests/,
so no production setting can turn a fake on (step 9 design, Tests).

    uv run --frozen python -m tests.e2e.server   # serves 127.0.0.1:8100 until stopped
"""

import asyncio
import signal
from pathlib import Path
from types import FrameType

import uvicorn
from testcontainers.community.postgres import PostgresContainer

from tests.integration.fake_forecast import FakeForecast
from tests.integration.fake_model import FakeModel
from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import POLICY
from tidehold.bootstrap.app import create_app
from tidehold.platform.db import connect
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import Settings
from tidehold.warnings import store

# The demo path's wording (web/e2e/demo-path.spec.ts): the fake model compiles it into one rule
# that conditions on a Gale Warning and proposes that event set, as a live model would.
GALE_CLAUSE = (
    "If a Gale Warning is in force and we cancel a dive day, you get a full refund for that day."
)
GALE_WORDING = f"Weather cancellations. {GALE_CLAUSE}"
GALE_RULE = {
    **POLICY["rules"][0],
    "warning": {
        "named_as": "Gale Warning",
        "source": "nws",
        "event_values": ["Gale Warning"],
        "quote": "a Gale Warning is in force",
    },
    "quote": GALE_CLAUSE,
}

# recorded official feeds committed with their licence lines (step 7): what a demo replays
RECORDED = Path(__file__).parent.parent / "fixtures" / "warnings"

PORT = 8100
# the Next.js server the browser loads (web/playwright.config.ts)
ORIGIN = "http://localhost:3100"


def _exit(signum: int, _: FrameType | None) -> None:
    # uvicorn re-raises the stop signal once it has shut down; turning it into SystemExit lets
    # the container's `with` block stop the database instead of the process dying inside it.
    # Later signals are ignored, so the stop is not interrupted and run twice.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise SystemExit(128 + signum)


async def _ingest(url: str) -> None:
    async with await connect(url) as conn:
        await store.ingest(conn, store.envelopes(sorted(RECORDED.glob("*.json.gz"))))


def main() -> None:
    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGINT, _exit)
    with PostgresContainer("postgres:18-alpine", driver=None) as pg:
        url = pg.get_connection_url()
        migrate(url)
        asyncio.run(_ingest(url))
        model = FakeModel()
        model.answer(POLICY["wording"], POLICY["rules"], [])
        model.answer(GALE_WORDING, [GALE_RULE], [])
        model.event_values = ["Gale Warning"]
        settings = Settings(database_url=url, public_origin=ORIGIN, run_worker=True)
        app = create_app(
            settings,
            paypal=FakePayPal().client(),
            compiler=model,
            forecasts=FakeForecast().client(),
        )
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
