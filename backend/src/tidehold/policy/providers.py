"""Model providers for the policy compiler: Groq (OpenAI-compatible) and Gemini, over HTTP.

Every call names an exact model ID (no aliases) and caps output tokens (D15). Provider content
is untrusted: callers validate it against the schema and locate every quote in the stored
wording. A refusal, an HTTP error or a timeout raises `ProviderError`; the caller decides what
happens next (the benchmark counts it, the product falls back to writing rules by hand).
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2 as httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_OUTPUT_TOKENS = 8192
TIMEOUT_S = 90.0
# Groq models with constrained decoding (strict structured outputs), console.groq.com/docs/
# structured-outputs, read 2026-10-19; others run best-effort and may return invalid JSON.
GROQ_STRICT = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"})


@dataclass(frozen=True)
class Completion:
    text: str
    latency_s: float


class ProviderError(Exception):
    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status

    @property
    def rate_limited(self) -> bool:
        return self.status == 429


class Provider(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion: ...


async def _post(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    try:
        response = await client.post(url, headers=headers, json=body, timeout=TIMEOUT_S)
    except httpx.HTTPError as error:
        raise ProviderError(type(error).__name__) from error
    latency = time.monotonic() - started
    if response.status_code != httpx.codes.OK:
        raise ProviderError(f"HTTP {response.status_code}", response.status_code)
    try:
        payload: dict[str, Any] = response.json()
    except ValueError as error:
        raise ProviderError("not JSON") from error
    return payload, latency


@dataclass(frozen=True)
class Groq:
    client: httpx.AsyncClient
    key: str
    model: str

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "strict": self.model in GROQ_STRICT,
                    "schema": schema,
                },
            },
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.key}"}
        payload, latency = await _post(self.client, GROQ_URL, headers, body)
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("unexpected response shape") from error
        if choice.get("finish_reason") != "stop" or not isinstance(text, str):
            raise ProviderError(f"finish_reason {choice.get('finish_reason')}")
        return Completion(text, latency)


@dataclass(frozen=True)
class Gemini:
    client: httpx.AsyncClient
    key: str
    model: str

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        del name  # Gemini takes the schema unnamed
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "temperature": 0,
            },
        }
        headers = {"x-goog-api-key": self.key}
        url = GEMINI_URL.format(model=self.model)
        payload, latency = await _post(self.client, url, headers, body)
        try:
            candidate = payload["candidates"][0]
            parts = candidate["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("unexpected response shape") from error
        if candidate.get("finishReason") != "STOP":
            raise ProviderError(f"finishReason {candidate.get('finishReason')}")
        return Completion(text, latency)
