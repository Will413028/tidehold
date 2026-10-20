"""Evaluate a bound policy on the operator's cancellation (plan step 8a, D16, D17).

Pure: the caller loads the rules, matches warning conditions against the evidence and reads
what the trip's earlier proposals returned. Code computes every amount (the model never
supplies one); whatever the policy leaves open becomes a reason to ask, with the options code
can compute, and past the trip's mandate the operator decides. Design and the semantics of
each reason: docs/plans/2026-10-19-tidehold-hackathon/step8a-design.md.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from math import floor
from typing import Any, Literal, get_args

from tidehold.policy.schema import Remedy, Rule

Purpose = Literal["deposit", "balance"]
PURPOSES: tuple[Purpose, ...] = ("deposit", "balance")
Reason = Literal[
    "NO_RULE",
    "CONFLICTING_RULES",
    "REMEDY_CHOICE",
    "WARNING_NOT_MATCHED",
    "RULED_IN_TEXT",
    "FIXED_DEDUCTION",
    "MANDATE_GUEST_CAP",
    "MANDATE_TRIP_CAP",
]
type Span = tuple[int, int]


@dataclass(frozen=True)
class RuleIn:
    """A rule of the trip's product with what the caller found out about it."""

    rule: Rule
    # a ruling's answer covers this clause in free text that code cannot read
    ruled_in_text: bool = False
    # None when the rule has no warning condition
    warning_matched: bool | None = None


@dataclass(frozen=True)
class Cancelled:
    scope: Literal["day", "rest"]
    cause: str
    first_day: int
    covered_days: int
    trip_days: int

    @property
    def whole_trip(self) -> bool:
        return self.scope == "rest" and self.first_day == 1


@dataclass(frozen=True)
class GuestMoney:
    guest_id: str
    # the deposit's net captured amount, and the authorized balance; 0 when not placed
    deposit_base: int
    balance_base: int
    # what the trip's accepted proposals already returned on each payment
    returned: Mapping[str, int] = field(default_factory=dict)

    def base(self, purpose: Purpose) -> int:
        return self.deposit_base if purpose == "deposit" else self.balance_base


@dataclass(frozen=True)
class Mandate:
    """The trip's caps, with what its accepted proposals already returned."""

    guest_minor: int
    trip_minor: int
    returned_guest: Mapping[str, int] = field(default_factory=dict)
    returned_trip: int = 0


@dataclass(frozen=True)
class Evaluation:
    reasons: list[Reason]
    # per purpose, the options; each {"label", "lines": [{guest_id, kind, amount_minor,
    # credit_expiry_months}]}, stored as the proposal's `parts`
    parts: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class Decision:
    state: Literal["accepted", "asked"]
    choice: dict[str, int] | None
    reasons: list[Reason]


@dataclass(frozen=True)
class _Option:
    label: str
    remedy: Remedy
    per_day: bool
    # indexes into the evaluated rules (the proposal's `facts.clauses`) that gave it
    rules: tuple[int, ...] = field(default=(), compare=False)


def _covers_cause(rule: Rule, cause: str) -> bool:
    return "any" in rule.causes or cause in rule.causes


def _applies(rule: Rule, purpose: Purpose) -> bool:
    return rule.applies_to in (purpose, "both")


def _candidates(
    rules: Sequence[RuleIn], cancelled: Cancelled, purpose: Purpose
) -> list[tuple[int, RuleIn]]:
    """A day fits per-day rules; the whole trip fits per-trip rules, else the per-day ones
    for every covered day; the rest of a trip that has begun fits per-day rules only. Each
    candidate keeps its index in `rules`."""
    eligible = [
        (i, r)
        for i, r in enumerate(rules)
        if _covers_cause(r.rule, cancelled.cause) and _applies(r.rule, purpose)
    ]
    per_trip = [(i, r) for i, r in eligible if r.rule.scope == "per_trip"]
    if cancelled.whole_trip and per_trip:
        return per_trip
    return [(i, r) for i, r in eligible if r.rule.scope == "per_day"]


def _label(remedy: Remedy) -> str:
    if remedy.kind == "none":
        return "no remedy"
    fixed = " before a fixed deduction" if any(d.kind == "fixed" for d in remedy.deductions) else ""
    return f"{remedy.kind} {remedy.percent:g}%{fixed}"


def _remedy(kind: Literal["refund", "credit", "none"], percent: int) -> Remedy:
    return Remedy(kind=kind, percent=percent, deductions=[], credit_expiry_months=None)


NO_REMEDY = _remedy("none", 0)
STANDARD = (NO_REMEDY, _remedy("refund", 100), _remedy("credit", 100))


def _amount(option: _Option, cancelled: Cancelled, guest: GuestMoney, purpose: Purpose) -> int:
    base = guest.base(purpose)
    if option.remedy.kind == "none" or base == 0:
        return 0
    share = Fraction(cancelled.covered_days, cancelled.trip_days) if option.per_day else 1
    deducted = sum(
        (Fraction(str(d.value)) for d in option.remedy.deductions if d.kind == "percent"),
        Fraction(0),
    )
    portion = base * share * Fraction(str(option.remedy.percent)) / 100 * (1 - deducted / 100)
    left = base - guest.returned.get(purpose, 0)
    return max(min(floor(portion), left), 0)


def _part(
    options: Sequence[_Option],
    cancelled: Cancelled,
    guests: Sequence[GuestMoney],
    purpose: Purpose,
) -> list[dict[str, Any]]:
    return [
        {
            "label": option.label,
            "rules": list(option.rules),
            "lines": [
                {
                    "guest_id": guest.guest_id,
                    "kind": option.remedy.kind,
                    "amount_minor": _amount(option, cancelled, guest, purpose),
                    "credit_expiry_months": option.remedy.credit_expiry_months,
                    # what was paid or held when evaluated: 0 means not placed then
                    "base_minor": guest.base(purpose),
                }
                for guest in guests
            ],
        }
        for option in options
    ]


def _flags(candidate: RuleIn) -> set[Reason]:
    """What one covering rule leaves open on its own."""
    flags: set[Reason] = set()
    if len(candidate.rule.remedies) > 1:
        flags.add("REMEDY_CHOICE")
    if candidate.ruled_in_text:
        flags.add("RULED_IN_TEXT")
    if candidate.warning_matched is False:
        flags.add("WARNING_NOT_MATCHED")
    return flags


def _options(
    candidates: Sequence[tuple[int, RuleIn]], cancelled: Cancelled
) -> tuple[list[_Option], set[Reason]]:
    if not candidates:
        per_day = not cancelled.whole_trip
        return [_Option(_label(r), r, per_day) for r in STANDARD], {"NO_RULE"}
    reasons = set().union(*(_flags(c) for _, c in candidates))
    options: list[_Option] = []
    for index, candidate in candidates:
        for remedy in candidate.rule.remedies:
            option = _Option(_label(remedy), remedy, candidate.rule.scope == "per_day", (index,))
            if option not in options:
                options.append(option)
            else:  # the same remedy from another rule: one option, both sources
                at = options.index(option)
                if index not in options[at].rules:
                    options[at] = replace(options[at], rules=(*options[at].rules, index))
    if "WARNING_NOT_MATCHED" in reasons and all(o.remedy.kind != "none" for o in options):
        options.append(_Option(_label(NO_REMEDY), NO_REMEDY, per_day=True))
    if len({c.rule.model_dump_json(include={"remedies"}) for _, c in candidates}) > 1:
        reasons.add("CONFLICTING_RULES")
    if any(d.kind == "fixed" for o in options for d in o.remedy.deductions):
        reasons.add("FIXED_DEDUCTION")
    return options, reasons


def evaluate(
    rules: Sequence[RuleIn], cancelled: Cancelled, guests: Sequence[GuestMoney]
) -> Evaluation:
    reasons: set[Reason] = set()
    parts: dict[str, list[dict[str, Any]]] = {}
    for purpose in PURPOSES:
        options, why = _options(_candidates(rules, cancelled, purpose), cancelled)
        reasons |= why
        parts[purpose] = _part(options, cancelled, guests, purpose)
    order = get_args(Reason)
    return Evaluation(sorted(reasons, key=order.index), parts)


def returned_by(
    parts: Mapping[str, Sequence[Mapping[str, Any]]], choice: Mapping[str, int]
) -> dict[str, int]:
    """What the chosen options return per guest (refund, release and credit alike)."""
    totals: dict[str, int] = {}
    for purpose, index in choice.items():
        for line in parts[purpose][index]["lines"]:
            if line["kind"] != "none":
                totals[line["guest_id"]] = totals.get(line["guest_id"], 0) + line["amount_minor"]
    return totals


def mandate_reasons(
    parts: Mapping[str, Sequence[Mapping[str, Any]]], choice: Mapping[str, int], mandate: Mandate
) -> list[Reason]:
    returned = returned_by(parts, choice)
    reasons: list[Reason] = []
    if any(
        amount + mandate.returned_guest.get(guest, 0) > mandate.guest_minor
        for guest, amount in returned.items()
    ):
        reasons.append("MANDATE_GUEST_CAP")
    if sum(returned.values()) + mandate.returned_trip > mandate.trip_minor:
        reasons.append("MANDATE_TRIP_CAP")
    return reasons


def decide(evaluation: Evaluation, mandate: Mandate) -> Decision:
    """Accept only what leaves nothing to choose and fits the mandate; ask otherwise."""
    if evaluation.reasons or any(len(o) != 1 for o in evaluation.parts.values()):
        return Decision("asked", None, evaluation.reasons)
    choice = dict.fromkeys(evaluation.parts, 0)
    over = mandate_reasons(evaluation.parts, choice, mandate)
    if over:
        return Decision("asked", None, over)
    return Decision("accepted", choice, [])


def _overlaps(a: Span, b: Span) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def ruled_in_text(
    rule_span: Span, warning_span: Span | None, ruling_spans: Sequence[Span | None]
) -> bool:
    """A ruling answered in free text covers this rule when its quote overlaps the rule's
    clause or its warning condition; a ruling with no quote names no clause."""
    clauses = [rule_span] if warning_span is None else [rule_span, warning_span]
    return any(
        span is not None and any(_overlaps(span, clause) for clause in clauses)
        for span in ruling_spans
    )
