"""The backend the browser tests (web/e2e) run against: the real app on a throwaway
PostgreSQL 18, with PayPal, the model and Open-Meteo replaced by the integration tests' fakes
and the due-action worker on. It lives under tests/, so no production setting can turn a fake
on (step 9 design, Tests).

    uv run --frozen python -m tests.e2e.server   # serves 127.0.0.1:8100 until stopped
"""

import signal
from types import FrameType

import uvicorn
from testcontainers.community.postgres import PostgresContainer

from tests.integration.fake_forecast import FakeForecast
from tests.integration.fake_model import FakeModel
from tests.integration.fake_paypal import FakePayPal
from tests.integration.payloads import POLICY
from tidehold.bootstrap.app import create_app
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import Settings

PORT = 8100
# the Next.js server the browser loads (web/playwright.config.ts)
ORIGIN = "http://localhost:3100"


def _exit(signum: int, _: FrameType | None) -> None:
    # uvicorn re-raises the stop signal once it has shut down; turning it into SystemExit lets
    # the container's `with` block stop the database instead of the process dying inside it
    raise SystemExit(128 + signum)


def main() -> None:
    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGINT, _exit)
    with PostgresContainer("postgres:18-alpine", driver=None) as pg:
        url = pg.get_connection_url()
        migrate(url)
        model = FakeModel()
        model.answer(POLICY["wording"], POLICY["rules"], [])
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
