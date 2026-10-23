"""Model providers for the policy compiler: Groq (OpenAI-compatible) and Gemini over HTTP, and
Claude through Anthropic's SDK (D22).

Every call names an exact model ID (no aliases) and caps output tokens (D15). Provider content
is untrusted: callers validate it against the schema and locate every quote in the stored
wording. A refusal, an HTTP error or a timeout raises `ProviderError`; the caller decides what
happens next (the benchmark counts it, the product falls back to writing rules by hand).
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx2 as httpx

from tidehold.platform.settings import ModelKeys

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_OUTPUT_TOKENS = 8192
TIMEOUT_S = 90.0
# Claude thinks adaptively before it answers, and the thinking spends output tokens, so its
# cap is higher (D22). The call is not streamed and keeps TIMEOUT_S like the others; the
# benchmark shows whether Claude finishes inside it (developer, 2026-10-23).
CLAUDE_MAX_TOKENS = 16000
# Pinned, so no environment variable (ANTHROPIC_BASE_URL) can point the SDK elsewhere.
CLAUDE_URL = "https://api.anthropic.com"
# The rate limits a 429 can name, from the `anthropic-ratelimit-<limit>-remaining` headers.
CLAUDE_LIMITS = ("requests", "input-tokens", "output-tokens")
# Groq models with constrained decoding (strict structured outputs), console.groq.com/docs/
# structured-outputs, read 2026-10-19; others run best-effort and may return invalid JSON.
GROQ_STRICT = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"})
# Groq names the exhausted limit in its message ("tokens per minute (TPM)"); the D, as in RPD,
# marks a daily one. Gemini names it in the quota id ("...PerDayPerProjectPerModel-FreeTier").
GROQ_LIMIT = re.compile(r"\((RPM|RPD|TPM|TPD|ASH|ASD)\)")
GROQ_DAILY = frozenset({"RPD", "TPD", "ASD"})
OVERLOADED = 529  # Anthropic's "overloaded_error"


@dataclass(frozen=True)
class Completion:
    text: str
    latency_s: float


class ProviderError(Exception):
    def __init__(self, reason: str, status: int | None = None, *, daily: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        # A daily quota is spent: nothing succeeds before the provider's reset, so go elsewhere.
        self.daily = daily

    @property
    def rate_limited(self) -> bool:
        return self.status == httpx.codes.TOO_MANY_REQUESTS

    @property
    def transient(self) -> bool:
        """Worth one retry after a pause: a per-minute limit or an overloaded model (503, or
        Anthropic's 529)."""
        overloaded = self.status in (httpx.codes.SERVICE_UNAVAILABLE, OVERLOADED)
        return (self.rate_limited or overloaded) and not self.daily


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
        detail, daily = _error_detail(response)
        reason = f"HTTP {response.status_code} {detail}".strip()
        raise ProviderError(reason, response.status_code, daily=daily)
    try:
        payload: dict[str, Any] = response.json()
    except ValueError as error:
        raise ProviderError("not JSON") from error
    return payload, latency


def _error_detail(response: httpx.Response) -> tuple[str, bool]:
    """The provider's own error code and the limits it names; never its free-text message,
    which can echo organisation IDs or the model's failed output."""
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return "", False
    if not isinstance(error, dict):
        return "", False
    # Groq: {"code": "json_validate_failed" | "rate_limit_exceeded", "message": "... (TPM) ..."}
    limits = GROQ_LIMIT.findall(str(error.get("message", "")))
    # Gemini: {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": ...}]}]}
    quotas = [
        str(violation.get("quotaId"))
        for detail in error.get("details") or []
        if isinstance(detail, dict)
        for violation in detail.get("violations") or []
        if isinstance(violation, dict)
    ]
    code = error.get("code") if isinstance(error.get("code"), str) else error.get("status")
    daily = any(limit in GROQ_DAILY for limit in limits) or any("PerDay" in q for q in quotas)
    return " ".join(str(part) for part in (code or "", *limits, *quotas) if part), daily


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


def claude_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` as Claude's structured outputs take it: the SDK keeps only the keywords they
    accept and moves bounds such as `minimum` into the description; the pydantic models still
    check every bound on the answer (structured-outputs docs, read 2026-10-23)."""
    return anthropic.transform_schema(schema)


def _field(body: object, *path: str) -> object:
    """The value at `path` in a JSON body, or None."""
    for key in path:
        if not isinstance(body, dict):
            return None
        body = body.get(key)
    return body


def _claude_error(error: anthropic.AnthropicError | ValueError) -> ProviderError:
    """Classified from the SDK's exception types, the error type and code in the body and the
    rate-limit headers; never the message text, which can echo account details. A timeout or
    connection error, any other SDK error, and an answer that is not JSON (a ValueError from the
    SDK's decoder) are named by their class."""
    if not isinstance(error, anthropic.APIStatusError):
        return ProviderError(type(error).__name__)
    code = _field(error.body, "error", "details", "error_code")
    parts = [str(error.type or "")]
    # Credit or payment trouble: nothing succeeds until a person fixes the account.
    daily = error.type == "billing_error"
    if isinstance(error, anthropic.RateLimitError):
        # The monthly spend cap: no retry-after, nothing succeeds before the month ends.
        daily = code == "enforced_spend_limit_reached"
        headers = error.response.headers
        parts += (
            [str(code)]
            if daily
            else [
                limit
                for limit in CLAUDE_LIMITS
                if headers.get(f"anthropic-ratelimit-{limit}-remaining") == "0"
            ]
        )
    reason = " ".join(part for part in (f"HTTP {error.status_code}", *parts) if part)
    return ProviderError(reason, error.status_code, daily=daily)


@dataclass(frozen=True)
class Claude:
    client: httpx.AsyncClient
    key: str = field(repr=False)
    model: str
    # Sent as `anthropic-workspace-id` when the key needs it (D22).
    workspace: str | None = field(default=None, repr=False)

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        del name  # Claude takes the schema unnamed
        sdk = anthropic.AsyncAnthropic(
            api_key=self.key,
            base_url=CLAUDE_URL,
            http_client=self.client,
            # Retries and fallback are the caller's, as for the other providers.
            max_retries=0,
            timeout=TIMEOUT_S,
            default_headers={"anthropic-workspace-id": self.workspace} if self.workspace else None,
            open_telemetry=False,
        )
        started = time.monotonic()
        try:
            # No temperature, top_p or top_k (refused since 2026-10-21) and no `thinking`
            # (adaptive by default; disabling it is refused on these models).
            message = await sdk.messages.create(
                model=self.model,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "format": {"type": "json_schema", "schema": claude_schema(schema)},
                    "effort": "medium",
                },
            )
        except (anthropic.AnthropicError, ValueError) as error:
            raise _claude_error(error) from error
        latency = time.monotonic() - started
        if message.stop_reason == "refusal":
            category = message.stop_details.category if message.stop_details else None
            raise ProviderError(f"refusal {category or 'none'}")
        if message.stop_reason != "end_turn":
            raise ProviderError(f"stop_reason {message.stop_reason}")
        text = "".join(block.text for block in message.content if block.type == "text")
        return Completion(text, latency)


VENDORS: dict[str, type[Groq] | type[Gemini] | type[Claude]] = {
    "groq": Groq,
    "gemini": Gemini,
    "anthropic": Claude,
}
# Exact model IDs measured by the compile benchmark (`model-benchmark.md`, D6, D15); the
# product runs only these, so a provider cannot swap the model behind an alias.
BENCHMARKED = frozenset(
    {"groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b", "groq:qwen/qwen3.8-27b"}
)


Built = Groq | Gemini | Claude


def build(spec: str, keys: ModelKeys) -> Built | None:
    """The product's provider for `vendor:model`, or None without its key (the no-AI path). A
    model the benchmark did not measure is refused even without a key, so a typo fails at
    start."""
    if spec not in BENCHMARKED:
        raise ValueError(f"model {spec!r} is not one the benchmark measured")
    vendor, model = spec.split(":", 1)
    if vendor == "anthropic":
        key = keys.anthropic
        return Claude(httpx.AsyncClient(), key, model, keys.anthropic_workspace) if key else None
    key = keys.groq if vendor == "groq" else keys.gemini
    return VENDORS[vendor](httpx.AsyncClient(), key, model) if key else None
