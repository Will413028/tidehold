import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
import pytest

from tidehold.policy.compile import CompileFailed, compile_policy, propose_event_set
from tidehold.policy.providers import Completion, Gemini, Groq, ProviderError
from tidehold.policy.schema import WIRE_SCHEMA
from tidehold.policy.spans import locate
from tidehold.warnings.cap import OfficialWarning
from tidehold.warnings.catalogue import GROUPS, event_value, values

RSQUO, NDASH = chr(0x2019), chr(0x2013)  # typographic apostrophe, en dash
# Scuba Tribe's weather clause as published (typographic apostrophe), divingpanglao.com,
# fetched 2026-10-19.
WORDING = (
    "Weather-related cancellations:\n"
    f"If we cancel the trip due to weather or safety reasons, you{RSQUO}ll receive a full refund "
    "(minus 3.5% processing fee for online payments) or the option to reschedule to another "
    "available date."
)
QUOTE = (
    "If we cancel the trip due to weather or safety reasons, you'll receive a full refund "
    "(minus 3.5% processing fee for online payments)"
)


def test_a_quote_with_ascii_apostrophes_is_found_in_typographic_wording() -> None:
    span = locate(QUOTE, WORDING)

    assert span is not None
    assert WORDING[span.start : span.end].startswith("If we cancel")
    assert WORDING[span.start : span.end].endswith("online payments)")
    assert RSQUO in WORDING[span.start : span.end]


def test_whitespace_and_dashes_are_folded_but_words_are_not() -> None:
    assert locate(f"Weather{NDASH}related   cancellations:  If we", WORDING) is not None
    assert locate("If we cancel the trip due to bad weather", WORDING) is None
    assert locate("you will receive a full refund", WORDING) is None
    assert locate("   ", WORDING) is None


@dataclass
class Scripted:
    """A provider that returns prepared answers in order and records what it was asked."""

    answers: list[str]
    model: str = "scripted"
    asked: list[str] = field(default_factory=list)

    async def complete(
        self, system: str, user: str, schema: dict[str, Any], name: str
    ) -> Completion:
        del system, schema, name
        self.asked.append(user)
        return Completion(self.answers.pop(0), 0.5)


def _rule(quote: str, **overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "causes": ["weather", "safety"],
        "warning": None,
        "scope": "per_trip",
        "applies_to": "both",
        "remedies": [
            {
                "kind": "refund",
                "percent": 100,
                "deductions": [{"kind": "percent", "value": 3.5, "label": "online payment fee"}],
                "credit_expiry_months": None,
            },
            {"kind": "credit", "percent": 100, "deductions": [], "credit_expiry_months": None},
        ],
        "chooser": "guest",
        "quote": quote,
    }
    return rule | overrides


def _answer(*rules: dict[str, Any], rulings: list[dict[str, Any]] | None = None) -> str:
    return json.dumps({"rules": list(rules), "needs_ruling": rulings or []})


async def test_a_compiled_rule_keeps_its_offsets_in_the_stored_wording() -> None:
    provider = Scripted([_answer(_rule(QUOTE))])

    compiled = await compile_policy(provider, WORDING, ["nws", "meteoalarm", "pagasa"])

    [located] = compiled.rules
    assert located.rule.chooser == "guest"
    assert WORDING[located.span.start : located.span.end].endswith("online payments)")
    assert compiled.needs_ruling == []
    assert compiled.attempts == 1


async def test_a_fabricated_quote_is_retried_once_then_becomes_a_ruling() -> None:
    fabricated = _rule("Guests always receive a full cash refund for any weather.")
    provider = Scripted([_answer(fabricated), _answer(fabricated)])

    compiled = await compile_policy(provider, WORDING, ["nws"])

    assert compiled.rules == []
    assert [r.ruling.topic for r in compiled.needs_ruling] == ["span_not_found"]
    assert compiled.attempts == 2
    assert "Guests always receive" in provider.asked[1]


async def test_a_retry_that_quotes_correctly_keeps_the_rule() -> None:
    provider = Scripted([_answer(_rule("a full cash refund")), _answer(_rule(QUOTE))])

    compiled = await compile_policy(provider, WORDING, ["nws"])

    assert len(compiled.rules) == 1
    assert compiled.needs_ruling == []


async def test_event_values_are_never_taken_at_compile_time() -> None:
    warning = {
        "named_as": "weather or safety reasons",
        "source": None,
        "event_values": ["Gale Warning"],
        "quote": "due to weather or safety reasons",
    }
    provider = Scripted([_answer(_rule(QUOTE, warning=warning))])

    [located] = (await compile_policy(provider, WORDING, ["nws"])).rules

    assert located.rule.warning is not None
    assert located.rule.warning.event_values == []
    assert located.warning_span is not None


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        json.dumps({"rules": [], "needs_ruling": [], "amount_usd": 120}),
        _answer(_rule(QUOTE, remedies=[])),
        _answer(
            _rule(
                QUOTE,
                warning={"named_as": "x", "source": "bmkg", "event_values": [], "quote": "weather"},
            )
        ),
    ],
)
async def test_an_answer_outside_the_schema_or_the_catalogue_is_refused(answer: str) -> None:
    with pytest.raises(CompileFailed):
        await compile_policy(Scripted([answer]), WORDING, ["nws"])


async def test_a_proposed_event_set_keeps_only_catalogue_values() -> None:
    answer = json.dumps({"event_values": ["Gale Warning", "Storm Warning", "Mega Storm"]})

    proposal = await propose_event_set(
        Scripted([answer]), "a gale warning or worse", "nws", GROUPS["nws"]
    )

    assert proposal.values == ["Gale Warning", "Storm Warning"]
    assert proposal.refused == ["Mega Storm"]


def _warning(source: Any, event: str, parameters: dict[str, str] | None = None) -> OfficialWarning:
    now = datetime(2026, 10, 19, tzinfo=UTC)
    return OfficialWarning(
        source=source,
        identifier="x",
        sent=now,
        onset=now,
        expires=now,
        event=event,
        severity="Moderate",
        msg_type="Alert",
        status="Actual",
        headline="",
        area_desc="",
        area_codes=("A",),
        url="",
        parameters=parameters or {},
    )


def test_recorded_warnings_map_to_catalogue_values() -> None:
    assert event_value(_warning("nws", "Gale Warning")) in values("nws")
    yellow_wind = {"awareness_type": "1; Wind", "awareness_level": "2; yellow; Moderate"}
    assert event_value(_warning("meteoalarm", "Yellow wind warning", yellow_wind)) == "Wind:yellow"
    assert event_value(_warning("pagasa", "General Flood Advisory (Final)")) in values("pagasa")


def _recording(seen: list[httpx.Request], answer: httpx.Response) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_groq_calls_name_the_exact_model_cap_tokens_and_send_the_schema() -> None:
    seen: list[httpx.Request] = []
    reply = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    groq = Groq(_recording(seen, httpx.Response(200, json=reply)), "k", "openai/gpt-oss-120b")

    completion = await groq.complete("sys", "user", WIRE_SCHEMA, "compiled_policy")

    body = json.loads(seen[0].content)
    assert completion.text == "{}"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["max_completion_tokens"] > 0
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == WIRE_SCHEMA


async def test_gemini_calls_cap_tokens_and_send_the_schema() -> None:
    seen: list[httpx.Request] = []
    reply = {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]}
    gemini = Gemini(_recording(seen, httpx.Response(200, json=reply)), "k", "gemini-3.5-flash")

    await gemini.complete("sys", "user", WIRE_SCHEMA, "compiled_policy")

    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/models/gemini-3.5-flash:generateContent")
    assert body["generationConfig"]["maxOutputTokens"] > 0
    assert body["generationConfig"]["responseJsonSchema"] == WIRE_SCHEMA


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (httpx.Response(429, json={}), "rate limited"),
        (httpx.Response(500, json={}), "error"),
        (
            httpx.Response(
                200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
            ),
            "error",
        ),
    ],
)
async def test_a_provider_failure_is_a_provider_error(answer: httpx.Response, kind: str) -> None:
    groq = Groq(_recording([], answer), "k", "openai/gpt-oss-20b")

    with pytest.raises(ProviderError) as raised:
        await groq.complete("sys", "user", WIRE_SCHEMA, "x")

    assert raised.value.rate_limited is (kind == "rate limited")
