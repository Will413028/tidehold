"""The Claude provider through Anthropic's SDK, against a mock transport: no real API call
(plan step 5c, D22)."""

import json
from collections.abc import Callable
from typing import Any

import httpx2 as httpx
import pytest

from tidehold.policy.compile import CompileFailed, compile_policy, propose_event_set
from tidehold.policy.providers import CLAUDE_MAX_TOKENS, Claude, ProviderError, claude_schema
from tidehold.policy.schema import EVENT_SET_SCHEMA, WIRE_SCHEMA
from tidehold.settlement import drafts

Handler = Callable[[httpx.Request], httpx.Response]
WORDING = "If we cancel the trip for weather, you receive a full refund."


def _message(text: str = "{}", stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": text},
        ],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        **extra,
    }


class Recorder:
    def __init__(self, handle: Handler) -> None:
        self.requests: list[httpx.Request] = []
        self.handle = handle

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handle(request)


async def _complete(handle: Handler, workspace: str | None = None) -> tuple[Recorder, str]:
    seen = Recorder(handle)
    async with httpx.AsyncClient(transport=httpx.MockTransport(seen)) as client:
        provider = Claude(client, "k", "claude-haiku-5-5", workspace)
        completion = await provider.complete("system", "user", WIRE_SCHEMA, "compiled_policy")
    return seen, completion.text


def _keys(schema: Any) -> set[str]:
    if isinstance(schema, dict):
        return set(schema) | {k for v in schema.values() for k in _keys(v)}
    if isinstance(schema, list):
        return {k for item in schema for k in _keys(item)}
    return set()


async def test_the_request_names_the_exact_model_its_format_and_effort_and_no_sampling() -> None:
    seen, text = await _complete(lambda _: httpx.Response(200, json=_message('{"a": 1}')))

    [request] = seen.requests
    body = json.loads(request.content)
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == CLAUDE_MAX_TOKENS
    assert body["system"] == "system"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    # Claude refuses numeric and length bounds; the schema is otherwise the same.
    assert not _keys(body["output_config"]["format"]["schema"]) & {"minimum", "maximum"}
    assert _keys(WIRE_SCHEMA) & {"minimum", "maximum"}
    assert not set(body) & {"temperature", "top_p", "top_k", "thinking", "fallbacks"}
    assert "anthropic-workspace-id" not in request.headers
    assert text == '{"a": 1}'  # thinking blocks are skipped


async def test_a_workspace_is_sent_when_the_key_needs_one_and_never_printed() -> None:
    seen, _ = await _complete(lambda _: httpx.Response(200, json=_message()), workspace="wrk_1")

    assert seen.requests[0].headers["anthropic-workspace-id"] == "wrk_1"
    async with httpx.AsyncClient() as client:
        printed = repr(Claude(client, "sk-secret", "m", "wrk_1"))
    assert "wrk_1" not in printed
    assert "sk-secret" not in printed


async def test_the_answer_goes_through_the_schema_validation() -> None:
    rule: dict[str, Any] = {
        "product": None,
        "causes": ["weather"],
        "warning": None,
        "scope": "per_trip",
        "applies_to": "both",
        "remedies": [
            {"kind": "refund", "percent": 150, "deductions": [], "credit_expiry_months": None}
        ],
        "chooser": None,
        "quote": "you receive a full refund",
    }
    answer = json.dumps({"rules": [rule], "needs_ruling": []})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_message(answer)))
    ) as client:
        provider = Claude(client, "k", "claude-haiku-5-5")
        # A percent over 100 is still refused: the conversion loosens nothing.
        with pytest.raises(CompileFailed, match="percent"):
            await compile_policy(provider, WORDING, ["nws"])
    rule["remedies"][0]["percent"] = 100
    valid = json.dumps({"rules": [rule], "needs_ruling": []})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_message(valid)))
    ) as client:
        compiled = await compile_policy(Claude(client, "k", "claude-haiku-5-5"), WORDING, ["nws"])
    assert [r.rule.remedies[0].percent for r in compiled.rules] == [100]


def _status(
    status: int, error_type: str, headers: dict[str, str] | None = None, **body: Any
) -> Handler:
    error = {"type": error_type, "message": "free text that may name the account"} | body
    payload = {"type": "error", "error": error}
    return lambda _: httpx.Response(status, json=payload, headers=headers or {})


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


# (reason, status, transient, daily)
Kind = tuple[str, int | None, bool, bool]


@pytest.mark.parametrize(
    ("handle", "kind"),
    [
        (
            lambda _: httpx.Response(
                200,
                json=_message("", "refusal", stop_details={"type": "refusal", "category": "cyber"}),
            ),
            ("refusal cyber", None, False, False),
        ),
        (
            lambda _: httpx.Response(200, json=_message('{"rules": [', "max_tokens")),
            ("stop_reason max_tokens", None, False, False),
        ),
        (
            _status(
                429,
                "rate_limit_error",
                {
                    "retry-after": "20",
                    "anthropic-ratelimit-requests-remaining": "120",
                    "anthropic-ratelimit-output-tokens-remaining": "0",
                },
            ),
            ("HTTP 429 rate_limit_error output-tokens", 429, True, False),
        ),
        (
            _status(
                429, "rate_limit_error", details={"error_code": "enforced_spend_limit_reached"}
            ),
            ("HTTP 429 rate_limit_error enforced_spend_limit_reached", 429, False, True),
        ),
        (_status(500, "api_error"), ("HTTP 500 api_error", 500, False, False)),
        (_status(529, "overloaded_error"), ("HTTP 529 overloaded_error", 529, True, False)),
        (_timeout, ("APITimeoutError", None, False, False)),
        (
            lambda _: httpx.Response(200, json=_message() | {"content": None}),
            ("no text in the answer", None, False, False),
        ),
        # credit or payment trouble: spent until a person fixes the account
        (_status(402, "billing_error"), ("HTTP 402 billing_error", 402, False, True)),
        (
            lambda _: httpx.Response(
                200, content=b"<html>", headers={"content-type": "application/json"}
            ),
            ("JSONDecodeError", None, False, False),
        ),
    ],
)
async def test_each_failure_is_a_provider_error_of_its_kind_sent_once(
    handle: Handler, kind: Kind
) -> None:
    seen = Recorder(handle)
    async with httpx.AsyncClient(transport=httpx.MockTransport(seen)) as client:
        provider = Claude(client, "k", "claude-haiku-5-5")
        with pytest.raises(ProviderError) as raised:
            await provider.complete("s", "u", WIRE_SCHEMA, "n")

    error = raised.value
    assert (error.reason, error.status, error.transient, error.daily) == kind
    assert "free text" not in error.reason
    assert len(seen.requests) == 1  # no retry inside the provider: the caller decides


def test_every_schema_sent_to_claude_keeps_only_keywords_it_accepts() -> None:
    refused = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    refused |= {"minLength", "maxLength", "pattern"}
    for schema in (WIRE_SCHEMA, EVENT_SET_SCHEMA, drafts.SCHEMA):
        assert not _keys(claude_schema(schema)) & refused


async def test_an_event_set_is_proposed_through_claude() -> None:
    answer = json.dumps({"event_values": ["Gale Warning", "Made-up Warning"]})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_message(answer)))
    ) as client:
        proposal = await propose_event_set(
            Claude(client, "k", "claude-haiku-5-5"),
            "a gale warning",
            "nws",
            [["Gale Warning", "Storm Warning"]],
        )

    assert (proposal.values, proposal.refused) == (["Gale Warning"], ["Made-up Warning"])


async def test_no_environment_variable_points_the_sdk_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-environment")

    seen, _ = await _complete(lambda _: httpx.Response(200, json=_message()))

    [request] = seen.requests
    assert request.url.host == "api.anthropic.com"
    assert request.headers["x-api-key"] == "k"
