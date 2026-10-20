"""The product runs only benchmarked model IDs with the benchmarked prompt, and caps every
call's output (D6, D15, threat model surface 3)."""

import json
import re
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from tidehold.platform.settings import DEFAULT_COMPILE_MODEL, ModelKeys
from tidehold.policy.prompt import PROMPT_HASH
from tidehold.policy.providers import BENCHMARKED, MAX_OUTPUT_TOKENS, VENDORS, Groq, build
from tidehold.policy.schema import WIRE_SCHEMA

BENCHMARK = (
    Path(__file__).resolve().parents[3]
    / "docs/plans/2026-10-19-tidehold-hackathon/model-benchmark.md"
)
# Per vendor: a successful reply, and where its request body carries the output-token cap.
REPLIES: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "groq": (
        {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
        ("max_completion_tokens",),
    ),
    "gemini": (
        {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}]},
        ("generationConfig", "maxOutputTokens"),
    ),
}


@pytest.mark.parametrize("vendor", sorted(VENDORS))
async def test_every_provider_call_caps_its_output_tokens(vendor: str) -> None:
    reply, path = REPLIES[vendor]  # a new vendor without an entry here fails
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=reply)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await VENDORS[vendor](client, "k", "some-model").complete("s", "u", WIRE_SCHEMA, "n")

    body = json.loads(seen[0].content)
    for key in path:
        body = body[key]
    assert body == MAX_OUTPUT_TOKENS


def test_the_configured_model_and_prompt_are_the_last_benchmarked() -> None:
    # The newest run comes first; older runs follow under "## Earlier run" headings.
    newest = BENCHMARK.read_text().split("\n## Earlier run")[0]
    latest = re.search(r"Prompt hash `([0-9a-f]{16})`", newest)
    measured = set(re.findall(r"^\| `([a-z]+:[^`]+)` \|", newest, re.MULTILINE))

    assert latest is not None
    assert latest.group(1) == PROMPT_HASH
    assert DEFAULT_COMPILE_MODEL in BENCHMARKED
    assert measured >= BENCHMARKED


async def test_only_a_benchmarked_model_id_is_built() -> None:
    with pytest.raises(ValueError, match="not one the benchmark measured"):
        build("groq:openai/gpt-oss", ModelKeys(groq="k"))
    assert build(DEFAULT_COMPILE_MODEL, ModelKeys()) is None

    provider = build(DEFAULT_COMPILE_MODEL, ModelKeys(groq="k"))

    assert isinstance(provider, Groq)
    assert provider.model == "openai/gpt-oss-120b"
    await provider.client.aclose()
