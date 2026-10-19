import dataclasses

import httpx2 as httpx
import pytest

from tidehold.money.paypal import (
    SANDBOX_API,
    PayPalClient,
    PayPalError,
    PayPalUnexpectedResponse,
    PayPalUnknownOutcome,
    capture_of,
    usd,
)
from tidehold.platform.settings import Settings, paypal_from_env

TOKEN = httpx.Response(200, json={"access_token": "t", "expires_in": 3600})


def _client(answer: httpx.Response | Exception, seen: list[httpx.Request]) -> PayPalClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return TOKEN
        if isinstance(answer, Exception):
            raise answer
        return answer

    return PayPalClient("id", "secret", transport=httpx.MockTransport(handle))


async def test_the_client_talks_only_to_the_sandbox() -> None:
    seen: list[httpx.Request] = []
    client = _client(httpx.Response(204), seen)

    await client.void("A1", request_id="tidehold-void-1")

    assert client.base_url == SANDBOX_API
    assert {r.url.host for r in seen} == {"api-m.sandbox.paypal.com"}
    assert "PayPal-Request-Id" in seen[-1].headers
    await client.aclose()


def test_no_setting_can_point_tidehold_at_another_paypal() -> None:
    assert not [
        f.name for f in dataclasses.fields(Settings) if "url" in f.name and f.name != "database_url"
    ]


def test_paypal_credentials_are_refused_outside_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "not-printed")
    monkeypatch.setenv("PAYPAL_ENV", "live")
    with pytest.raises(ValueError, match="sandbox"):
        paypal_from_env()

    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    credentials = paypal_from_env()
    assert "not-printed" not in repr(credentials)


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(503), httpx.ConnectError("down"), httpx.ReadTimeout("slow")],
)
async def test_an_outage_or_timeout_is_an_unknown_outcome(
    answer: httpx.Response | Exception,
) -> None:
    client = _client(answer, [])

    with pytest.raises(PayPalUnknownOutcome):
        await client.void("A1", request_id="tidehold-void-1")
    await client.aclose()


async def test_a_refusal_names_paypals_issue() -> None:
    body = {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "AUTHORIZATION_VOIDED"}]}
    client = _client(httpx.Response(422, json=body), [])

    with pytest.raises(PayPalError, match="PAYPAL_AUTHORIZATION_VOIDED"):
        await client.void("A1", request_id="tidehold-void-1")
    await client.aclose()


def test_an_answer_without_a_capture_is_unexpected_not_failed() -> None:
    with pytest.raises(PayPalUnexpectedResponse):
        capture_of({"id": "O1", "purchase_units": [{"payments": {}}]})


def test_amounts_are_sent_as_usd_decimal_strings() -> None:
    assert usd(30005) == {"currency_code": "USD", "value": "300.05"}
