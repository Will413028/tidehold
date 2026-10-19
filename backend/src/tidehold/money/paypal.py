"""PayPal Orders v2 / Payments v2 client, pinned to the sandbox.

Ported from dive-trip-agent@8065eba `modules/deposits/paypal.py`; tidehold adds CAPTURE
orders (the booking deposit, D3), reauthorization (D11) and order lookup for reconciling.

Every write carries a caller-supplied PayPal-Request-Id, so a retry with the same key
returns the original result instead of moving money twice. Transport errors, 5xx answers
and 2xx answers we cannot read raise PayPalUnknownOutcome: the caller retries with the same
key, never with a fresh one.
"""

import base64
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

import httpx2 as httpx

from tidehold.platform.errors import Conflict, OutcomeUnknown

# Not configurable: no setting can point tidehold at live PayPal.
SANDBOX_API = "https://api-m.sandbox.paypal.com"

Intent = Literal["CAPTURE", "AUTHORIZE"]


class PayPalError(Conflict):
    """PayPal answered and refused the request; no money moved for this key."""

    def __init__(self, status: int, issue: str) -> None:
        self.status = status
        self.issue = issue
        super().__init__(f"PAYPAL_{issue}")


class PayPalUnknownOutcome(OutcomeUnknown):
    """The request may or may not have taken effect."""

    def __init__(self, code: str = "PAYPAL_OUTCOME_UNKNOWN") -> None:
        super().__init__(code)


class PayPalUnexpectedResponse(PayPalUnknownOutcome):
    """PayPal accepted the request but answered in a shape we cannot record, so money may
    have moved: the command stays open for reconciliation rather than closing."""


@dataclass(frozen=True)
class SandboxCard:
    number: str
    expiry: str
    security_code: str
    name: str


# PayPal's public sandbox test Visa; it only works against the sandbox.
TEST_CARD = SandboxCard("4111111111111111", "2030-12", "123", "Sandbox Guest")


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    status: str
    amount_minor: int
    expires_at: str | None


@dataclass(frozen=True)
class Capture:
    capture_id: str
    status: str
    amount_minor: int


@dataclass(frozen=True)
class Refund:
    refund_id: str
    status: str
    amount_minor: int


def usd(minor: int) -> dict[str, str]:
    if minor < 0:
        raise ValueError("negative amount")
    return {"currency_code": "USD", "value": f"{minor // 100}.{minor % 100:02d}"}


@contextmanager
def answered(code: str) -> Iterator[None]:
    """Read a 2xx answer. PayPal accepted the request, so an answer we cannot read leaves
    its outcome unknown rather than failed."""
    try:
        yield
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise PayPalUnexpectedResponse(code) from error


def minor_of(amount: dict[str, Any]) -> int:
    if amount.get("currency_code") != "USD":
        raise PayPalUnexpectedResponse("PAYPAL_CURRENCY_UNEXPECTED")
    whole, _, cents = str(amount["value"]).partition(".")
    return int(whole) * 100 + int((cents + "00")[:2])


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._http = httpx.AsyncClient(
            base_url=SANDBOX_API, transport=transport, timeout=timeout_seconds
        )
        self._token: str | None = None
        self._token_expires = 0.0

    @property
    def base_url(self) -> str:
        return str(self._http.base_url).rstrip("/")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        response = await self._send(
            "POST",
            "/v1/oauth2/token",
            headers={"Authorization": f"Basic {self._basic}"},
            data={"grant_type": "client_credentials"},
        )
        with answered("PAYPAL_TOKEN_UNREADABLE"):
            body = response.json()
            self._token = str(body["access_token"])
            self._token_expires = time.monotonic() + int(body.get("expires_in", 0)) - 60
        return self._token

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TransportError as error:
            raise PayPalUnknownOutcome from error
        if response.status_code >= 500:
            raise PayPalUnknownOutcome
        if response.status_code >= 400:
            try:
                body = response.json() if response.content else {}
                details = body.get("details") or [{}]
                issue = details[0].get("issue") or body.get("name") or "REJECTED"
            except (ValueError, AttributeError, IndexError):
                issue = "REJECTED"
            raise PayPalError(response.status_code, str(issue))
        return response

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {await self._access_token()}",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        response = await self._send(method, path, json=body, headers=headers)
        with answered("PAYPAL_RESPONSE_UNREADABLE"):
            result: dict[str, Any] = response.json() if response.content else {}
            return result

    async def create_order(
        self,
        *,
        intent: Intent,
        invoice_id: str,
        amount_minor: int,
        request_id: str,
        card: SandboxCard | None = None,
    ) -> dict[str, Any]:
        """Without a card, an order the buyer approves in PayPal's checkout (no money moves
        until we capture or authorize it). With the server's sandbox card, PayPal processes
        the order at once: the answer carries the capture or the authorization."""
        body: dict[str, Any] = {
            "intent": intent,
            "purchase_units": [{"invoice_id": invoice_id, "amount": usd(amount_minor)}],
        }
        if card is not None:
            body["payment_source"] = {
                "card": {
                    "number": card.number,
                    "expiry": card.expiry,
                    "security_code": card.security_code,
                    "name": card.name,
                }
            }
        return await self._call("POST", "/v2/checkout/orders", body, request_id)

    async def capture_order(self, order_id: str, *, request_id: str) -> dict[str, Any]:
        return await self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id)

    async def authorize_order(self, order_id: str, *, request_id: str) -> dict[str, Any]:
        return await self._call("POST", f"/v2/checkout/orders/{order_id}/authorize", {}, request_id)

    async def capture(
        self,
        authorization_id: str,
        *,
        amount_minor: int,
        final: bool,
        invoice_id: str,
        request_id: str,
    ) -> Capture:
        capture = await self._call(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            {"amount": usd(amount_minor), "final_capture": final, "invoice_id": invoice_id},
            request_id,
        )
        with answered("PAYPAL_CAPTURE_UNEXPECTED"):
            return Capture(str(capture["id"]), str(capture["status"]), minor_of(capture["amount"]))

    async def void(self, authorization_id: str, *, request_id: str) -> None:
        await self._call(
            "POST", f"/v2/payments/authorizations/{authorization_id}/void", {}, request_id
        )

    async def reauthorize(
        self, authorization_id: str, *, amount_minor: int, request_id: str
    ) -> Authorization:
        auth = await self._call(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/reauthorize",
            {"amount": usd(amount_minor)},
            request_id,
        )
        with answered("PAYPAL_REAUTHORIZATION_UNEXPECTED"):
            return Authorization(
                str(auth["id"]),
                str(auth["status"]),
                minor_of(auth["amount"]),
                auth.get("expiration_time"),
            )

    async def refund(
        self, capture_id: str, *, amount_minor: int, invoice_id: str, request_id: str
    ) -> Refund:
        refund = await self._call(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            {"amount": usd(amount_minor), "invoice_id": invoice_id},
            request_id,
        )
        with answered("PAYPAL_REFUND_UNEXPECTED"):
            return Refund(str(refund["id"]), str(refund["status"]), minor_of(refund["amount"]))

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v2/checkout/orders/{order_id}")

    async def get_authorization(self, authorization_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v2/payments/authorizations/{authorization_id}")

    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v2/payments/captures/{capture_id}")


def order_id_of(order: dict[str, Any]) -> str:
    with answered("PAYPAL_ORDER_UNEXPECTED"):
        return str(order["id"])


def _first_payment(order: dict[str, Any], kind: str) -> dict[str, Any]:
    units = order.get("purchase_units") or []
    payments = (units[0].get("payments") or {}) if units else {}
    found = payments.get(kind) or []
    if not found:
        raise PayPalUnexpectedResponse(f"PAYPAL_{kind.upper()}_MISSING")
    first: dict[str, Any] = found[0]
    return first


def authorization_of(order: dict[str, Any]) -> Authorization:
    with answered("PAYPAL_AUTHORIZATION_UNEXPECTED"):
        auth = _first_payment(order, "authorizations")
        return Authorization(
            str(auth["id"]),
            str(auth["status"]),
            minor_of(auth["amount"]),
            auth.get("expiration_time"),
        )


def capture_of(order: dict[str, Any]) -> Capture:
    with answered("PAYPAL_CAPTURE_UNEXPECTED"):
        capture = _first_payment(order, "captures")
        return Capture(str(capture["id"]), str(capture["status"]), minor_of(capture["amount"]))
