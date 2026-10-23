"""What the model returns when it compiles cancellation wording (D16, plan step 2).

A rule pays out only after the operator records a cancellation with a cause the rule covers; a
warning condition adds an official warning the rule also requires. Remedies carry percentages,
never amounts: code computes every amount. `WIRE_SCHEMA` is the JSON Schema sent to providers
(written out by hand: strict structured output needs every property required, no `$ref`, and
`additionalProperties: false`); the pydantic models validate what comes back.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Cause = Literal["weather", "safety", "any"]
Scope = Literal["per_day", "per_trip"]
AppliesTo = Literal["deposit", "balance", "both"]
RemedyKind = Literal["refund", "credit", "none"]
Chooser = Literal["guest", "operator"]
RulingTopic = Literal[
    "warning_source_unnamed",
    "remedy_chooser_unclear",
    "outcome_unspecified",
    "operator_discretion",
    "no_weather_clause",
    "conflicting_clauses",
    "credit_expiry_unspecified",
    "deduction_unspecified",
    "deduction_conditional",
    "span_not_found",
    "other",
]
Percent = Annotated[float, Field(ge=0, le=100)]
Quote = Annotated[str, Field(min_length=1, max_length=600)]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Deduction(Wire):
    kind: Literal["percent", "fixed"]
    value: Annotated[float, Field(ge=0)]
    label: str
    # The passage that states the deduction, verbatim; verified like the rule's (step 5b).
    quote: Quote


class Remedy(Wire):
    kind: RemedyKind
    percent: Percent
    deductions: list[Deduction]
    credit_expiry_months: Annotated[int, Field(ge=1)] | None


class WarningCondition(Wire):
    # The authority as the wording names it ("local authorities", "PAGASA"), verbatim.
    named_as: str
    # The catalogue source it resolves to; null when the wording names none of them.
    source: str | None
    event_values: list[str]
    quote: Quote


class Rule(Wire):
    # The product heading the rule sits under ("Liveaboard Trips"), verbatim; null when the
    # wording covers one product. A trip binds the rules of its product (step 5).
    product: Annotated[str, Field(min_length=1, max_length=200)] | None
    causes: list[Cause]
    warning: WarningCondition | None
    scope: Scope
    applies_to: AppliesTo
    remedies: Annotated[list[Remedy], Field(min_length=1)]
    # Who picks when there is more than one remedy; null with one remedy or when unclear.
    chooser: Chooser | None
    quote: Quote


class Ruling(Wire):
    topic: RulingTopic
    question: str
    quote: Quote | None


class Compiled(Wire):
    rules: list[Rule]
    needs_ruling: list[Ruling]


class EventSet(Wire):
    event_values: list[str]


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(**properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _enum(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _literals(alias: Any) -> tuple[str, ...]:
    return tuple(alias.__args__)


_STRING = {"type": "string"}
_DEDUCTION = _object(
    kind=_enum(("percent", "fixed")),
    value={"type": "number", "minimum": 0},
    label=_STRING,
    quote=_STRING,
)
_REMEDY = _object(
    kind=_enum(_literals(RemedyKind)),
    percent={"type": "number", "minimum": 0, "maximum": 100},
    deductions={"type": "array", "items": _DEDUCTION},
    credit_expiry_months=_nullable({"type": "integer", "minimum": 1}),
)
_WARNING = _object(
    named_as=_STRING,
    source=_nullable(_STRING),
    event_values={"type": "array", "items": _STRING},
    quote=_STRING,
)
_RULE = _object(
    product=_nullable(_STRING),
    causes={"type": "array", "items": _enum(_literals(Cause))},
    warning=_nullable(_WARNING),
    scope=_enum(_literals(Scope)),
    applies_to=_enum(_literals(AppliesTo)),
    remedies={"type": "array", "items": _REMEDY},
    chooser=_nullable(_enum(_literals(Chooser))),
    quote=_STRING,
)
_RULING = _object(
    topic=_enum(_literals(RulingTopic)),
    question=_STRING,
    quote=_nullable(_STRING),
)
WIRE_SCHEMA: dict[str, Any] = _object(
    rules={"type": "array", "items": _RULE},
    needs_ruling={"type": "array", "items": _RULING},
)
EVENT_SET_SCHEMA: dict[str, Any] = _object(event_values={"type": "array", "items": _STRING})
