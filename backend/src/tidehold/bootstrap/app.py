from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from tidehold.bootstrap import routes
from tidehold.bootstrap.http_boundary import HttpBoundary
from tidehold.platform.errors import Conflict, DomainError, LimitReached, NotFound, OutcomeUnknown
from tidehold.platform.settings import Settings, from_env

STATUS_OF: dict[type[DomainError], int] = {
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    LimitReached: status.HTTP_429_TOO_MANY_REQUESTS,
    OutcomeUnknown: status.HTTP_503_SERVICE_UNAVAILABLE,
}


async def domain_error(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, DomainError):
        raise error
    code = next((s for kind, s in STATUS_OF.items() if isinstance(error, kind)), None)
    if code is None:  # an error no caller can act on: do not leak its code
        return JSONResponse({"detail": "INTERNAL_ERROR"}, status_code=500)
    return JSONResponse({"detail": error.code}, status_code=code)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()
    # No interactive docs: every route must be classified in the route table.
    app = FastAPI(title="tidehold", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.add_exception_handler(DomainError, domain_error)
    routes.mount(app)
    app.add_middleware(HttpBoundary, origin=settings.public_origin)
    return app
