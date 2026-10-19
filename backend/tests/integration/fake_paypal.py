"""An in-memory PayPal sandbox: the same PayPal-Request-Id returns the same answer."""

import json
from typing import Any
from uuid import uuid4

import httpx2 as httpx

from tidehold.money.paypal import PayPalClient


class FakePayPal:
    def __init__(self) -> None:
        self.answers: dict[str, httpx.Response] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        # Knobs a test turns to reproduce PayPal's failure modes.
        self.card_status = "CREATED"
        self.time_out_first: set[str] = set()  # path suffixes whose first call times out
        self.refuse: set[str] = set()  # path suffixes PayPal answers with 422

    def client(self) -> PayPalClient:
        return PayPalClient("id", "secret", transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        self.calls.append(f"{request.method} {path}")
        if request.method == "GET":
            return self._get(path)
        suffix = path.rsplit("/", 1)[-1]
        if suffix in self.refuse:
            return httpx.Response(
                422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "DECLINED"}]}
            )
        key = request.headers.get("PayPal-Request-Id", "")
        if key not in self.answers:
            body = json.loads(request.content) if request.content else {}
            self.answers[key] = self._answer(path, key, body)
            if suffix in self.time_out_first:
                self.time_out_first.discard(suffix)
                return httpx.Response(504)
        return self.answers[key]

    def moved(self, suffix: str) -> int:
        """How many distinct writes ending in `suffix` took effect."""
        return sum(1 for k in self.answers if k.endswith(suffix))

    def _get(self, path: str) -> httpx.Response:
        order = self.orders.get(path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=order) if order else httpx.Response(404)

    def _answer(self, path: str, key: str, body: dict[str, Any]) -> httpx.Response:
        if path == "/v2/checkout/orders":
            return self._create(key, body)
        action = path.rsplit("/", 1)[-1]
        if path.startswith("/v2/checkout/orders/"):
            order = self.orders[path.split("/")[-2]]
            if action == "capture":
                return httpx.Response(201, json=_with(order, "captures", _capture(order["amount"])))
            return httpx.Response(201, json=_with(order, "authorizations", self._auth(order)))
        return self._payment_action(action, body)

    def _payment_action(self, action: str, body: dict[str, Any]) -> httpx.Response:
        answers = {
            "capture": lambda: httpx.Response(201, json=_capture(body["amount"])),
            "void": lambda: httpx.Response(204),
            "reauthorize": lambda: httpx.Response(201, json=self._auth({"amount": body["amount"]})),
            "refund": lambda: httpx.Response(
                201,
                json={
                    "id": f"R{uuid4().hex[:15].upper()}",
                    "status": "COMPLETED",
                    "amount": body["amount"],
                },
            ),
        }
        answer = answers.get(action)
        return answer() if answer else httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def _create(self, key: str, body: dict[str, Any]) -> httpx.Response:
        unit = body["purchase_units"][0]
        order = {
            "id": f"O{uuid4().hex[:15].upper()}",
            "intent": body["intent"],
            "amount": unit["amount"],
            "status": "CREATED",
        }
        self.orders[order["id"]] = order
        if "payment_source" not in body:
            return httpx.Response(201, json=order)
        if body["intent"] == "CAPTURE":
            return httpx.Response(201, json=_with(order, "captures", _capture(unit["amount"])))
        return httpx.Response(201, json=_with(order, "authorizations", self._auth(order)))

    def _auth(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"A{uuid4().hex[:15].upper()}",
            "status": self.card_status,
            "amount": order["amount"],
            "expiration_time": "2026-12-31T00:00:00Z",
        }


def _capture(amount: dict[str, str]) -> dict[str, Any]:
    return {"id": f"C{uuid4().hex[:15].upper()}", "status": "COMPLETED", "amount": amount}


def _with(order: dict[str, Any], kind: str, payment: dict[str, Any]) -> dict[str, Any]:
    return {**order, "status": "COMPLETED", "purchase_units": [{"payments": {kind: [payment]}}]}
