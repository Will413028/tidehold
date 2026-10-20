"""Policy versions: the rules and rulings an operator confirmed from its wording (plan step 5).

The operator starts from a compilation (or from nothing, writing rules by hand: the no-AI
path) and publishes a version. Publishing checks what the money path relies on, whoever wrote
the rule: every quote is found in the stored wording, a warning condition names a catalogue
source and a non-empty set of that source's event values, two or more remedies say who
chooses, and every item the compiler raised has an answer. The version is then fixed (a
database trigger refuses any change) and trips bind it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from tidehold.platform.db import Conn, insert_returning
from tidehold.platform.errors import Invalid, NotFound
from tidehold.policy.schema import Rule, Ruling
from tidehold.policy.spans import Span, locate
from tidehold.warnings import catalogue

MAX_WORDING = 12_000
MAX_ANSWER = 2_000


@dataclass(frozen=True)
class Answer:
    """The operator's answer to the compiler's `index`-th needs-ruling item."""

    index: int
    answer: str


@dataclass(frozen=True)
class Checked:
    rules: list[dict[str, Any]]
    rulings: list[dict[str, Any]]
    warning_source: str | None


def _span(span: Span | None) -> list[int] | None:
    return None if span is None else [span.start, span.end]


def _located(quote: str, wording: str) -> Span:
    span = locate(quote, wording)
    if span is None:
        raise Invalid("SPAN_NOT_FOUND")
    return span


def _check_rule(rule: Rule, wording: str) -> dict[str, Any]:
    span = _located(rule.quote, wording)
    if rule.product is not None and locate(rule.product, wording) is None:
        raise Invalid("PRODUCT_NOT_FOUND")
    warning_span = None
    if rule.warning is not None:
        warning_span = _located(rule.warning.quote, wording)
        source = rule.warning.source
        if source is None:
            raise Invalid("WARNING_SOURCE_MISSING")
        if source not in catalogue.GROUPS:
            raise Invalid("WARNING_SOURCE_NOT_IN_CATALOGUE")
        if not rule.warning.event_values:
            raise Invalid("EVENT_SET_EMPTY")
        if not set(rule.warning.event_values) <= catalogue.values(source):
            raise Invalid("EVENT_VALUE_NOT_IN_CATALOGUE")
    if len(rule.remedies) > 1 and rule.chooser is None:
        raise Invalid("CHOOSER_MISSING")
    if any(r.kind == "none" and r.percent != 0 for r in rule.remedies):
        raise Invalid("REMEDY_NONE_WITH_PERCENT")
    return {"rule": rule.model_dump(), "span": _span(span), "warning_span": _span(warning_span)}


def _check_rulings(
    asked: Sequence[Ruling], answers: Sequence[Answer], wording: str
) -> list[dict[str, Any]]:
    by_index = {a.index: a.answer.strip() for a in answers}
    if len(by_index) != len(answers) or set(by_index) - set(range(len(asked))):
        raise Invalid("RULING_UNKNOWN")
    if any(not by_index.get(i) for i in range(len(asked))):
        raise Invalid("RULING_UNANSWERED")
    return [
        {
            "topic": ruling.topic,
            "question": ruling.question,
            "quote": ruling.quote,
            "span": _span(locate(ruling.quote, wording) if ruling.quote else None),
            "answer": by_index[i],
        }
        for i, ruling in enumerate(asked)
    ]


def check(
    wording: str, rules: Sequence[Rule], asked: Sequence[Ruling], answers: Sequence[Answer]
) -> Checked:
    """What a version stores, or `Invalid` naming the first check that failed."""
    if not wording.strip() or len(wording) > MAX_WORDING:
        raise Invalid("WORDING_LENGTH")
    if any(len(a.answer) > MAX_ANSWER for a in answers):
        raise Invalid("ANSWER_LENGTH")
    checked = [_check_rule(rule, wording) for rule in rules]
    sources = {rule.warning.source for rule in rules if rule.warning is not None}
    if len(sources) > 1:
        raise Invalid("MIXED_WARNING_SOURCES")
    return Checked(checked, _check_rulings(asked, answers, wording), next(iter(sources), None))


async def publish(
    conn: Conn, workspace_id: UUID, wording: str, checked: Checked, compilation_id: UUID | None
) -> UUID:
    row = await insert_returning(
        conn,
        "insert into policy_versions (workspace_id, wording, rules, rulings, warning_source,"
        " compilation_id) values (%s, %s, %s, %s, %s, %s) returning id",
        (
            workspace_id,
            wording,
            Jsonb(checked.rules),
            Jsonb(checked.rulings),
            checked.warning_source,
            compilation_id,
        ),
    )
    version_id: UUID = row["id"]
    return version_id


async def version(conn: Conn, workspace_id: UUID, version_id: UUID) -> DictRow:
    row = await (
        await conn.execute(
            "select * from policy_versions where id = %s and workspace_id = %s",
            (version_id, workspace_id),
        )
    ).fetchone()
    if row is None:
        raise NotFound
    return row


async def versions(conn: Conn, workspace_id: UUID) -> list[DictRow]:
    return await (
        await conn.execute(
            "select * from policy_versions where workspace_id = %s order by created_at, id",
            (workspace_id,),
        )
    ).fetchall()


def rules_of(row: DictRow) -> list[Rule]:
    return [Rule.model_validate(item["rule"]) for item in row["rules"]]


def products(row: DictRow) -> set[str]:
    """The product headings the version's rules sit under."""
    return {rule.product for rule in rules_of(row) if rule.product is not None}
