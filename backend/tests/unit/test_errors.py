import json

import pytest
from starlette.requests import Request

from tidehold.bootstrap.app import domain_error
from tidehold.platform.errors import Conflict, DomainError, LimitReached, NotFound, OutcomeUnknown


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (NotFound(), 404, "NOT_FOUND"),
        (Conflict("PAYMENT_ATTEMPT_IN_FLIGHT"), 409, "PAYMENT_ATTEMPT_IN_FLIGHT"),
        (LimitReached("PAYPAL_DAILY_CAP_REACHED"), 429, "PAYPAL_DAILY_CAP_REACHED"),
        (OutcomeUnknown("PAYPAL_OUTCOME_UNKNOWN"), 503, "PAYPAL_OUTCOME_UNKNOWN"),
        (DomainError("SOMETHING_INTERNAL"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_each_error_class_has_one_status(
    error: DomainError, status: int, detail: str
) -> None:
    response = await domain_error(Request({"type": "http"}), error)

    assert (response.status_code, json.loads(bytes(response.body))) == (status, {"detail": detail})
