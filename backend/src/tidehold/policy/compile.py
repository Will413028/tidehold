"""Compile an operator's cancellation wording into rules, and propose a warning condition's
event set (D16, plan steps 2 and 5).

The model's output is untrusted: it must match the schema, every quote must be found in the
stored wording (`spans.locate`), and a warning source or event value must come from the
catalogue the caller passes (`warnings.catalogue` in the product). A rule whose quote or warning
quote cannot be found, or whose deduction quote is not inside the rule's quote, after one
retry is not kept: it becomes a "span_not_found" ruling for the operator.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from tidehold.policy.prompt import compile_prompt, event_set_prompt
from tidehold.policy.providers import Provider
from tidehold.policy.schema import (
    EVENT_SET_SCHEMA,
    WIRE_SCHEMA,
    Compiled,
    EventSet,
    Rule,
    Ruling,
)
from tidehold.policy.spans import Span, locate, locate_within

# One answer and, when a quote is not found, one retry that names the missing quotes.
ATTEMPTS = 2


class CompileFailed(Exception):
    """The model's answer did not match the schema (or named a source outside the catalogue)."""


@dataclass(frozen=True)
class LocatedRule:
    rule: Rule
    span: Span
    warning_span: Span | None
    # per remedy, the span of each of its deductions' quotes
    deduction_spans: list[list[Span]] = field(default_factory=list)


@dataclass(frozen=True)
class LocatedRuling:
    ruling: Ruling
    span: Span | None


@dataclass(frozen=True)
class Compilation:
    rules: list[LocatedRule]
    needs_ruling: list[LocatedRuling]
    attempts: int
    latencies_s: list[float] = field(default_factory=list)


def _user(wording: str) -> str:
    return f"Cancellation wording, between the markers:\n<<<\n{wording}\n>>>"


def _parse(text: str, sources: Sequence[str]) -> Compiled:
    try:
        compiled = Compiled.model_validate_json(text)
    except ValidationError as error:
        raise CompileFailed(str(error.errors()[:3])) from error
    for rule in compiled.rules:
        if rule.warning and rule.warning.source is not None and rule.warning.source not in sources:
            raise CompileFailed(f"unknown source {rule.warning.source!r}")
    return compiled


def _missing(rule: Rule, wording: str) -> list[str]:
    """The quotes of a rule that was not kept: its own or its warning's when not in the
    wording, a deduction's when not inside the rule's quote."""
    quotes = [rule.quote, *([rule.warning.quote] if rule.warning else [])]
    missing = [q for q in quotes if locate(q, wording) is None]
    span = locate(rule.quote, wording)
    deductions = [d.quote for remedy in rule.remedies for d in remedy.deductions]
    return missing + [
        q for q in deductions if span is None or locate_within(q, wording, span) is None
    ]


def _locate_rule(rule: Rule, wording: str) -> LocatedRule | None:
    span = locate(rule.quote, wording)
    warning_span = locate(rule.warning.quote, wording) if rule.warning else None
    if span is None or (rule.warning is not None and warning_span is None):
        return None
    # A deduction is read inside its rule's clause (step 5b), so a ruling on it is a ruling on
    # the rule.
    deduction_spans: list[list[Span]] = []
    for remedy in rule.remedies:
        spans = [locate_within(d.quote, wording, span) for d in remedy.deductions]
        found = [s for s in spans if s is not None]
        if len(found) != len(spans):
            return None
        deduction_spans.append(found)
    # Event values are chosen with the operator (propose_event_set), never at compile time.
    if rule.warning and rule.warning.event_values:
        rule = rule.model_copy(
            update={"warning": rule.warning.model_copy(update={"event_values": []})}
        )
    return LocatedRule(rule, span, warning_span, deduction_spans)


def _retry_note(missing: list[str]) -> str:
    quotes = "\n".join(f"- {json.dumps(q, ensure_ascii=False)}" for q in missing)
    return (
        "\n\nYour previous answer quoted passages that are not in the wording (a deduction's "
        "quote must lie inside its rule's quote). Copy every quote exactly from the wording, "
        f"character for character:\n{quotes}"
    )


async def compile_policy(provider: Provider, wording: str, sources: Sequence[str]) -> Compilation:
    system = compile_prompt(list(sources))
    user = _user(wording)
    latencies: list[float] = []
    kept: list[LocatedRule] = []
    lost: list[Rule] = []
    compiled = Compiled(rules=[], needs_ruling=[])
    for attempt in range(1, ATTEMPTS + 1):
        completion = await provider.complete(system, user, WIRE_SCHEMA, "compiled_policy")
        latencies.append(completion.latency_s)
        compiled = _parse(completion.text, sources)
        located = [(rule, _locate_rule(rule, wording)) for rule in compiled.rules]
        kept = [found for _, found in located if found is not None]
        lost = [rule for rule, found in located if found is None]
        if not lost or attempt == ATTEMPTS:
            break
        user = _user(wording) + _retry_note([q for r in lost for q in _missing(r, wording)])
    rulings = [
        LocatedRuling(r, locate(r.quote, wording) if r.quote else None)
        for r in compiled.needs_ruling
    ]
    rulings += [
        LocatedRuling(
            Ruling(
                topic="span_not_found",
                question="The compiler proposed a rule it could not quote from your wording. "
                "Write this rule by hand or rephrase the wording.",
                quote=None,
            ),
            None,
        )
        for _ in lost
    ]
    return Compilation(kept, rulings, attempts=len(latencies), latencies_s=latencies)


@dataclass(frozen=True)
class EventSetProposal:
    values: list[str]
    # Values the model named that are not in the catalogue; never kept.
    refused: list[str]
    latency_s: float


async def propose_event_set(
    provider: Provider, quote: str, source: str, groups: Sequence[Sequence[str]]
) -> EventSetProposal:
    """The catalogue values the warning wording `quote` covers for `source`, whose catalogue
    is `groups` (each ladder least to most severe); the operator confirms them in a ruling
    before any trip binds the policy."""
    catalogue = [list(group) for group in groups]
    known = {value for group in catalogue for value in group}
    completion = await provider.complete(
        event_set_prompt(source, catalogue),
        f"Warning wording: {quote}",
        EVENT_SET_SCHEMA,
        "event_set",
    )
    try:
        proposed = EventSet.model_validate_json(completion.text).event_values
    except ValidationError as error:
        raise CompileFailed(str(error.errors()[:3])) from error
    values = list(dict.fromkeys(v for v in proposed if v in known))
    refused = [v for v in proposed if v not in known]
    return EventSetProposal(values, refused, completion.latency_s)
