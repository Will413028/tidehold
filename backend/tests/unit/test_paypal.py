import dataclasses
import time

import httpx2 as httpx
import pytest

from tidehold.bootstrap import nightly_audit
from tidehold.money.paypal import (
    SANDBOX_API,
    PayPalClient,
    PayPalError,
    PayPalUnexpectedResponse,
    PayPalUnknownOutcome,
    ReadOnlyRefused,
    ReadOnlyTransport,
    capture_of,
    refunds_of,
    usd,
)
from tidehold.platform.settings import PayPalCredentials, Settings, paypal_from_env

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

    await client.capture_order("O1", request_id="tidehold-open-1")

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
        await client.capture_order("O1", request_id="tidehold-open-1")
    await client.aclose()


async def test_a_refusal_names_paypals_issue() -> None:
    body = {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]}
    client = _client(httpx.Response(422, json=body), [])

    with pytest.raises(PayPalError, match="PAYPAL_ORDER_NOT_APPROVED"):
        await client.capture_order("O1", request_id="tidehold-open-1")
    await client.aclose()


EXPIRED = httpx.Response(401, json={"error": "invalid_token", "error_description": "expired"})


def _tokens_then(answers: list[httpx.Response], seen: list[httpx.Request]) -> PayPalClient:
    """A client whose every token request is answered with a new token, and whose other
    requests get `answers` in order."""
    issued = iter(range(1, 100))

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200, json={"access_token": f"t{next(issued)}", "expires_in": 3600}
            )
        return answers.pop(0)

    return PayPalClient("id", "secret", transport=httpx.MockTransport(handle))


async def test_a_token_paypal_no_longer_accepts_is_replaced_and_the_request_sent_once_more() -> (
    None
):
    seen: list[httpx.Request] = []
    client = _tokens_then([EXPIRED, httpx.Response(204)], seen)

    await client.capture_order("O1", request_id="tidehold-open-1")

    sent = [r for r in seen if r.url.path != "/v1/oauth2/token"]
    assert [r.headers["Authorization"] for r in sent] == ["Bearer t1", "Bearer t2"]
    assert {r.headers["PayPal-Request-Id"] for r in sent} == {"tidehold-open-1"}

    seen.clear()
    still = _tokens_then([EXPIRED, EXPIRED], seen)
    with pytest.raises(PayPalError) as refused:
        await still.capture_order("O1", request_id="tidehold-open-1")
    assert refused.value.status == 401
    assert len([r for r in seen if r.url.path != "/v1/oauth2/token"]) == 2
    await client.aclose()
    await still.aclose()


async def test_a_token_expires_by_the_wall_clock_so_a_sleeping_host_does_not_reuse_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # macOS stops time.monotonic while the machine sleeps; PayPal's expiry does not stop.
    seen: list[httpx.Request] = []
    client = _tokens_then([httpx.Response(204), httpx.Response(204)], seen)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    await client.capture_order("O1", request_id="tidehold-open-1")

    monkeypatch.setattr(time, "time", lambda: now + 3600)
    await client.capture_order("O1", request_id="tidehold-open-2")

    sent = [r for r in seen if r.url.path != "/v1/oauth2/token"]
    assert [r.headers["Authorization"] for r in sent] == ["Bearer t1", "Bearer t2"]
    await client.aclose()


def test_an_answer_without_a_capture_is_unexpected_not_failed() -> None:
    with pytest.raises(PayPalUnexpectedResponse):
        capture_of({"id": "O1", "purchase_units": [{"payments": {}}]})


def test_amounts_are_sent_as_usd_decimal_strings() -> None:
    assert usd(30005) == {"currency_code": "USD", "value": "300.05"}


async def test_reads_show_an_orders_refunds_by_invoice_id_and_a_refunds_status() -> None:
    order = {
        "id": "O1",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "captures": [{"id": "C1", "status": "COMPLETED", "amount": usd(3000)}],
                    "refunds": [
                        {
                            "id": "R1",
                            "status": "PENDING",
                            "amount": usd(1500),
                            "invoice_id": "tidehold-a",
                        }
                    ],
                }
            }
        ],
    }
    seen: list[httpx.Request] = []

    read = await _client(httpx.Response(200, json=order), seen).get_order("O1")

    assert [(r.refund_id, r.status, r.amount_minor, r.invoice_id) for r in refunds_of(read)] == [
        ("R1", "PENDING", 1500, "tidehold-a")
    ]
    assert refunds_of({"id": "O2", "purchase_units": [{"payments": {}}]}) == []
    assert (seen[-1].method, seen[-1].url.path) == ("GET", "/v2/checkout/orders/O1")
    pending = {"id": "R1", "status": "PENDING", "amount": usd(1500), "invoice_id": "tidehold-a"}
    refund = await _client(httpx.Response(200, json=pending), seen).get_refund("R1")
    assert (refund.status, seen[-1].url.path) == ("PENDING", "/v2/payments/refunds/R1")


async def test_a_capture_is_read_by_its_id() -> None:
    seen: list[httpx.Request] = []
    answer = httpx.Response(200, json={"id": "C1", "status": "PENDING", "amount": usd(3000)})

    capture = await _client(answer, seen).get_capture("C1")

    assert (capture.capture_id, capture.status, capture.amount_minor) == ("C1", "PENDING", 3000)
    assert (seen[-1].method, seen[-1].url.path) == ("GET", "/v2/payments/captures/C1")


async def test_the_read_only_transport_reads_but_never_sends() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return TOKEN
        return httpx.Response(200, json={"id": "O1", "status": "COMPLETED"})

    client = PayPalClient("id", "secret", transport=ReadOnlyTransport(httpx.MockTransport(handle)))

    assert (await client.get_order("O1"))["id"] == "O1"
    with pytest.raises(ReadOnlyRefused):
        await client.capture_order("O1", request_id="tidehold-open-1")
    with pytest.raises(ReadOnlyRefused):
        await client.refund("C1", amount_minor=100, request_id="tidehold-refund-1", invoice_id="i")
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/v1/oauth2/token"),
        ("GET", "/v2/checkout/orders/O1"),
    ]


async def test_the_nightly_audit_client_is_read_only() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return TOKEN

    client = nightly_audit.read_only_client(
        PayPalCredentials("id", "secret"), httpx.MockTransport(handle)
    )

    with pytest.raises(ReadOnlyRefused):
        await client.capture_order("O1", request_id="tidehold-open-1")
    assert [r.url.path for r in seen] == ["/v1/oauth2/token"]
