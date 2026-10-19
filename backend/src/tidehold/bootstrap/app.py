import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tidehold.bootstrap import routes, worker
from tidehold.bootstrap.http_boundary import HttpBoundary
from tidehold.money.paypal import PayPalClient
from tidehold.platform.errors import (
    Conflict,
    DomainError,
    Invalid,
    LimitReached,
    NotConfigured,
    NotFound,
    Unavailable,
)
from tidehold.platform.settings import Settings, from_env

STATUS_OF: dict[type[DomainError], int] = {
    Invalid: status.HTTP_400_BAD_REQUEST,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    LimitReached: status.HTTP_429_TOO_MANY_REQUESTS,
    NotConfigured: status.HTTP_501_NOT_IMPLEMENTED,
    Unavailable: status.HTTP_503_SERVICE_UNAVAILABLE,
}


async def domain_error(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, DomainError):
        raise error
    code = next((s for kind, s in STATUS_OF.items() if isinstance(error, kind)), None)
    if code is None:  # an error no caller can act on: do not leak its code
        return JSONResponse({"detail": "INTERNAL_ERROR"}, status_code=500)
    return JSONResponse({"detail": error.code}, status_code=code)


async def invalid_request(_: Request, __: Exception) -> JSONResponse:
    """One answer for any malformed request; FastAPI's default would echo the input back."""
    return JSONResponse({"detail": "INVALID_REQUEST"}, status_code=400)


def create_app(settings: Settings | None = None, *, paypal: PayPalClient | None = None) -> FastAPI:
    settings = settings or from_env()
    if paypal is None and settings.paypal is not None:
        paypal = PayPalClient(settings.paypal.client_id, settings.paypal.client_secret)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if settings.run_worker:
            task = asyncio.create_task(
                worker.run_forever(settings.database_url, worker.handlers(paypal))
            )
        yield
        if task is not None:
            task.cancel()
        if paypal is not None:
            await paypal.aclose()

    # No interactive docs: every route must be classified in the route table.
    app = FastAPI(
        title="tidehold", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.paypal = paypal
    app.state.paypal_client_id = settings.paypal.client_id if settings.paypal else None
    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(RequestValidationError, invalid_request)
    routes.mount(app)
    app.add_middleware(HttpBoundary, origin=settings.public_origin)
    return app
