from fastapi import FastAPI

from tidehold.identity import sessions
from tidehold.platform.settings import Settings, from_env
from tidehold.trips import http as trips


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="tidehold")
    app.state.settings = settings or from_env()
    app.include_router(sessions.router)
    app.include_router(trips.router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
