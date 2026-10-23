"""An in-memory PayPal sandbox: the same PayPal-Request-Id returns the same answer, and reads
of an order, a capture or a refund show what the writes did."""

import json
from typing import Any
from uuid import uuid4

import httpx2 as httpx

from tidehold.money.paypal import PayPalClient


class FakePayPal:
    def __init__(self) -> None:
        self.answers: dict[str, httpx.Response] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.captures: dict[str, dict[str, Any]] = {}  # capture id -> capture, with its order
        self.refunds: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        # Knobs a test turns to reproduce PayPal's failure modes.
        self.card_status = "CREATED"
        self.refund_status = "COMPLETED"
        # path suffixes whose first call takes effect but its answer is lost (a timeout) ...
        self.time_out_first: set[str] = set()
        # ... or times out without taking effect
        self.lose_first: set[str] = set()
        self.refuse: set[str] = set()  # path suffixes PayPal answers with 422
        self.fail_reads: set[str] = set()  # path prefixes whose reads answer 503

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
        if suffix in self.lose_first:
            self.lose_first.discard(suffix)
            return httpx.Response(504)
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

    def posts(self, suffix: str) -> int:
        """How many POSTs to a path ending in `suffix` reached PayPal (resends included)."""
        return sum(1 for c in self.calls if c.startswith("POST") and c.endswith(suffix))

    def reads(self, prefix: str) -> int:
        """How many reads of a path starting with `prefix` reached PayPal."""
        return sum(1 for c in self.calls if c.startswith(f"GET {prefix}"))

    def settle_refunds(self, status: str = "COMPLETED") -> None:
        """PayPal finishes every pending refund."""
        for refund in self.refunds.values():
            if refund["status"] == "PENDING":
                refund["status"] = status

    def _get(self, path: str) -> httpx.Response:
        if any(path.startswith(prefix) for prefix in self.fail_reads):
            return httpx.Response(503)
        kind, _, ident = path.rpartition("/")
        found = {
            "/v2/checkout/orders": self.orders,
            "/v2/payments/captures": self.captures,
            "/v2/payments/refunds": self.refunds,
        }.get(kind, {}).get(ident)
        if found is None:
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})
        return httpx.Response(200, json=found)

    def _answer(self, path: str, key: str, body: dict[str, Any]) -> httpx.Response:
        if path == "/v2/checkout/orders":
            return self._create(key, body)
        action = path.rsplit("/", 1)[-1]
        if path.startswith("/v2/checkout/orders/"):
            order = self.orders[path.split("/")[-2]]
            if action == "capture":
                return httpx.Response(201, json=self._capture(order, order["amount"]))
            return httpx.Response(201, json=self._place(order, "authorizations", self._auth(order)))
        if path.startswith("/v2/payments/captures/") and action == "refund":
            return self._refund(path.split("/")[-2], body)
        return self._payment_action(action, body)

    def _payment_action(self, action: str, body: dict[str, Any]) -> httpx.Response:
        answers = {
            "capture": lambda: httpx.Response(201, json=_capture(body["amount"])),
            "void": lambda: httpx.Response(204),
            "reauthorize": lambda: httpx.Response(201, json=self._auth({"amount": body["amount"]})),
        }
        answer = answers.get(action)
        return answer() if answer else httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def _refund(self, capture_id: str, body: dict[str, Any]) -> httpx.Response:
        refund = {
            "id": f"R{uuid4().hex[:15].upper()}",
            "status": self.refund_status,
            "amount": body["amount"],
            "invoice_id": body.get("invoice_id"),
        }
        self.refunds[refund["id"]] = refund
        order = self.captures.get(capture_id, {}).get("order")
        if order is not None:
            payments = order["purchase_units"][0]["payments"]
            payments.setdefault("refunds", []).append(refund)
        return httpx.Response(201, json=refund)

    def _create(self, key: str, body: dict[str, Any]) -> httpx.Response:
        unit = body["purchase_units"][0]
        order = {
            "id": f"O{uuid4().hex[:15].upper()}",
            "intent": body["intent"],
            "amount": unit["amount"],
            "status": "CREATED",
            "purchase_units": [{"amount": unit["amount"], "payments": {}}],
        }
        self.orders[order["id"]] = order
        if "card" not in body.get("payment_source", {}):
            links = [{"rel": "payer-action", "href": f"https://sandbox.example/{order['id']}"}]
            return httpx.Response(201, json={**order, "links": links})
        if body["intent"] == "CAPTURE":
            return httpx.Response(201, json=self._capture(order, unit["amount"]))
        return httpx.Response(201, json=self._place(order, "authorizations", self._auth(order)))

    def _capture(self, order: dict[str, Any], amount: dict[str, str]) -> dict[str, Any]:
        capture = _capture(amount)
        self.captures[capture["id"]] = {**capture, "order": order}
        return self._place(order, "captures", capture)

    def _place(self, order: dict[str, Any], kind: str, payment: dict[str, Any]) -> dict[str, Any]:
        """The order as PayPal shows it after placing a capture or a hold (kept for reads)."""
        order["status"] = "COMPLETED"
        order["purchase_units"][0]["payments"].setdefault(kind, []).append(payment)
        shown: dict[str, Any] = json.loads(json.dumps(order))
        return shown

    def _auth(self, order: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"A{uuid4().hex[:15].upper()}",
            "status": self.card_status,
            "amount": order["amount"],
            "expiration_time": "2026-12-31T00:00:00Z",
        }


def _capture(amount: dict[str, str]) -> dict[str, Any]:
    return {"id": f"C{uuid4().hex[:15].upper()}", "status": "COMPLETED", "amount": amount}
