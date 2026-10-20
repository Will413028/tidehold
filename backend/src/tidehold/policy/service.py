"""Compile wording and propose event sets through the model, under the daily cap, falling
back to the no-AI path (plan step 5, D16).

The model is optional on every path: with no provider configured, past the cap with nothing
to replay, or when the call fails, the answer says so and the operator writes rules or picks
event values by hand; the same publish checks apply either way (`versions.check`). The result
of a call is stored before it is shown, so a replay shows exactly what the live call returned.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg.rows import DictRow

from tidehold.platform.db import Conn
from tidehold.platform.errors import Invalid
from tidehold.policy import calls
from tidehold.policy.compile import (
    Compilation,
    CompileFailed,
    compile_policy,
    propose_event_set,
)
from tidehold.policy.prompt import PROMPT_HASH
from tidehold.policy.providers import Provider, ProviderError
from tidehold.policy.schema import Ruling
from tidehold.policy.spans import Span
from tidehold.warnings import catalogue

Mode = Literal["live", "recorded", "no_ai"]
# Why there is no model answer; the operator continues by hand.
Reason = Literal["AI_NOT_CONFIGURED", "AI_DAILY_CAP_REACHED", "AI_UNAVAILABLE"]
SOURCES: tuple[str, ...] = tuple(catalogue.GROUPS)


@dataclass(frozen=True)
class Context:
    """Who is asking, through which model (None: not configured), under which cap."""

    conn: Conn
    workspace_id: UUID
    provider: Provider | None
    cap: calls.Cap


@dataclass(frozen=True)
class Outcome:
    """A model answer (live or recorded) with its call, or why there is none."""

    mode: Mode
    reason: Reason | None
    call: DictRow | None

    @property
    def result(self) -> dict[str, Any] | None:
        return None if self.call is None else self.call["result"]


def _span(span: Span | None) -> list[int] | None:
    return None if span is None else [span.start, span.end]


def compilation_result(wording: str, compilation: Compilation) -> dict[str, Any]:
    return {
        "wording": wording,
        "rules": [
            {
                "rule": located.rule.model_dump(),
                "span": _span(located.span),
                "warning_span": _span(located.warning_span),
            }
            for located in compilation.rules
        ],
        "needs_ruling": [
            {"ruling": located.ruling.model_dump(), "span": _span(located.span)}
            for located in compilation.needs_ruling
        ],
        "attempts": compilation.attempts,
        "latencies_s": compilation.latencies_s,
    }


def asked(result: dict[str, Any]) -> list[Ruling]:
    """The needs-ruling items of a stored compilation, in the order the operator answers."""
    return [Ruling.model_validate(item["ruling"]) for item in result["needs_ruling"]]


Work = Callable[[Provider], Awaitable[dict[str, Any]]]


async def run(
    ctx: Context, purpose: calls.Purpose, payload: object, work: Work, *, prompt_hash: str
) -> Outcome:
    """One model call under the cap, with replay and the no-AI fallback; `prompt_hash` names
    the prompt `work` sends (compiling and drafting asks use different prompts)."""
    provider = ctx.provider
    if provider is None:
        return Outcome("no_ai", "AI_NOT_CONFIGURED", None)
    ask = calls.Ask(
        ctx.workspace_id,
        purpose,
        provider.model,
        prompt_hash,
        calls.digest(purpose, provider.model, prompt_hash, payload),
    )
    call = await calls.start(ctx.conn, ask, ctx.cap)
    if call["mode"] == "recorded":
        return Outcome("recorded", None, call)
    if call["mode"] == "capped":
        return Outcome("no_ai", "AI_DAILY_CAP_REACHED", None)
    try:
        result = await work(provider)
    except (CompileFailed, ProviderError) as error:
        await calls.finish(ctx.conn, call["id"], result=None, error_code=_error_code(error))
        replayed = await calls.replay(ctx.conn, ask, ctx.cap.today)
        if replayed is not None:
            return Outcome("recorded", None, replayed)
        return Outcome("no_ai", "AI_UNAVAILABLE", None)
    await calls.finish(ctx.conn, call["id"], result=result, error_code=None)
    finished = await calls.succeeded(ctx.conn, ctx.workspace_id, call["id"], purpose)
    return Outcome("live", None, finished)


def _error_code(error: CompileFailed | ProviderError) -> str:
    if isinstance(error, ProviderError):
        return f"ProviderError: {error.reason}"
    return "CompileFailed"


async def compile_wording(ctx: Context, wording: str) -> Outcome:
    async def work(model: Provider) -> dict[str, Any]:
        return compilation_result(wording, await compile_policy(model, wording, SOURCES))

    return await run(ctx, "compile", [wording, list(SOURCES)], work, prompt_hash=PROMPT_HASH)


async def propose(ctx: Context, source: str, quote: str) -> Outcome:
    groups = catalogue_of(source)

    async def work(model: Provider) -> dict[str, Any]:
        proposal = await propose_event_set(model, quote, source, groups)
        return {"values": proposal.values, "refused": proposal.refused}

    payload = [source, quote, groups]
    return await run(ctx, "event_set", payload, work, prompt_hash=PROMPT_HASH)


def catalogue_of(source: str) -> list[list[str]]:
    """The source's event values by hazard, least to most severe, then the rest."""
    if source not in catalogue.GROUPS:
        raise Invalid("WARNING_SOURCE_NOT_IN_CATALOGUE")
    return [list(group) for group in catalogue.GROUPS[source]]


async def compilation_for(
    conn: Conn, workspace_id: UUID, compilation_id: UUID, wording: str
) -> DictRow:
    """The workspace's compilation of exactly this wording."""
    call = await calls.succeeded(conn, workspace_id, compilation_id, "compile")
    if call["result"]["wording"] != wording:
        raise Invalid("COMPILATION_NOT_FOR_WORDING")
    return call
