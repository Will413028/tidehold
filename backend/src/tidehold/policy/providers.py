"""The model provider for the policy compiler and drafts: Claude through Anthropic's SDK (D22).

Every call names an exact model ID (no aliases) and caps output tokens (D15). Provider content
is untrusted: callers validate it against the schema and locate every quote in the stored
wording. A refusal, an HTTP error or a timeout raises `ProviderError`; the caller decides what
happens next (the benchmark counts it, the product falls back to writing rules by hand).
"""

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx2 as httpx

from tidehold.platform.settings import ModelKeys

TIMEOUT_S = 90.0
# Claude thinks adaptively before it answers, and the thinking spends output tokens (D22). The
# call is not streamed; the benchmark shows whether Claude finishes inside TIMEOUT_S
# (developer, 2026-10-23).
CLAUDE_MAX_TOKENS = 16000
# Pinned, so no environment variable (ANTHROPIC_BASE_URL) can point the SDK elsewhere.
CLAUDE_URL = "https://api.anthropic.com"
# The rate limits a 429 can name, from the `anthropic-ratelimit-<limit>-remaining` headers.
CLAUDE_LIMITS = ("requests", "input-tokens", "output-tokens")
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
        # Converted outside the `try`: a fault in our own schema is not a provider error.
        converted = claude_schema(schema)
        sdk = anthropic.AsyncAnthropic(
            api_key=self.key,
            base_url=CLAUDE_URL,
            http_client=self.client,
            # Retries and fallback are the caller's.
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
                    "format": {"type": "json_schema", "schema": converted},
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
        # The SDK does not check a response's shape: a 200 without content is a provider error.
        blocks = message.content or []
        text = "".join(b.text for b in blocks if isinstance(b, anthropic.types.TextBlock))
        if not text:
            raise ProviderError("no text in the answer")
        return Completion(text, latency)


VENDORS: dict[str, type[Claude]] = {"anthropic": Claude}
# Exact model IDs measured by the compile benchmark (`model-benchmark.md`, D6, D15); the
# product runs only these, so a provider cannot swap the model behind an alias.
BENCHMARKED = frozenset({"anthropic:claude-haiku-5-5", "anthropic:claude-sonnet-5-5"})


def build(spec: str, keys: ModelKeys) -> Claude | None:
    """The product's provider for `vendor:model`, or None without its key (the no-AI path). A
    model the benchmark did not measure is refused even without a key, so a typo fails at
    start."""
    if spec not in BENCHMARKED:
        raise ValueError(f"model {spec!r} is not one the benchmark measured")
    model = spec.split(":", 1)[1]
    key = keys.anthropic
    return Claude(httpx.AsyncClient(), key, model, keys.anthropic_workspace) if key else None
