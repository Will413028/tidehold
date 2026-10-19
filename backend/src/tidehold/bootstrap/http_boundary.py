"""Checks every request before routing, decoding or database access.

Ported from dive-trip-agent@8065eba `bootstrap/http_boundary.py`. State-changing requests
must come from the configured origin (defence in depth behind the SameSite=Lax cookie) and
carry JSON when they carry a body; bodies are capped; every response gets cache and
sniffing headers.
"""

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY = 32 * 1024
METHODS = ("GET", "POST", "DELETE")
UNSAFE = ("POST", "DELETE")
SECURITY_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


class _Refused(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status


class HttpBoundary:
    def __init__(self, app: ASGIApp, origin: str) -> None:
        self.app, self.origin = app, origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *SECURITY_HEADERS]}
            await send(message)

        try:
            body = await self._checked_body(scope, receive)
        except _Refused as refused:
            response = JSONResponse({"detail": refused.code}, status_code=refused.status)
            await response(scope, receive, secure_send)
            return
        if body is None:  # a safe method: nothing to buffer
            await self.app(scope, receive, secure_send)
            return
        delivered = False

        async def replay_body() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_body, secure_send)

    async def _checked_body(self, scope: Scope, receive: Receive) -> bytes | None:
        """The buffered body of an accepted state-changing request; None for a safe one."""
        if scope["method"] not in METHODS:
            raise _Refused("METHOD_NOT_ALLOWED", 405)
        if scope["method"] not in UNSAFE:
            return None
        refusal = self._refusal(scope["headers"])
        if refusal is not None:
            raise _Refused(refusal)
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _Refused("CLIENT_DISCONNECTED")
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY:
                raise _Refused("BODY_TOO_LARGE")
            more = message.get("more_body", False)
        if body and not _is_json(scope["headers"]):
            raise _Refused("INVALID_CONTENT_TYPE")
        return bytes(body)

    def _refusal(self, raw: list[tuple[bytes, bytes]]) -> str | None:
        # A repeated header would let the proxy and this check read different values.
        for name, code in (
            (b"origin", "INVALID_ORIGIN"),
            (b"content-type", "INVALID_CONTENT_TYPE"),
            (b"content-length", "BODY_TOO_LARGE"),
        ):
            if sum(key.lower() == name for key, _ in raw) > 1:
                return code
        headers = {key.lower(): value.decode("latin-1") for key, value in raw}
        if headers.get(b"origin") != self.origin:
            return "INVALID_ORIGIN"
        size = headers.get(b"content-length")
        if size is not None and (not re.fullmatch(r"[0-9]{1,8}", size) or int(size) > MAX_BODY):
            return "BODY_TOO_LARGE"
        return None


def _is_json(raw: list[tuple[bytes, bytes]]) -> bool:
    for key, value in raw:
        if key.lower() == b"content-type":
            return value.decode("latin-1").split(";")[0].strip().lower() == "application/json"
    return False
