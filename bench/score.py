"""Scores for the compile benchmark, as `GUIDELINE.md` defines them."""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

Key = tuple[Any, ...]


def rule_key(rule: Mapping[str, Any]) -> Key:
    """What must be equal for two rules to match; the product and the quotes are not scored."""
    warning = rule.get("warning")
    remedies = sorted(
        (
            r["kind"],
            round(float(r["percent"]), 2),
            tuple(sorted((d["kind"], round(float(d["value"]), 2)) for d in r["deductions"])),
            r.get("credit_expiry_months"),
        )
        for r in rule["remedies"]
    )
    return (
        tuple(sorted(set(rule["causes"]))),
        None if warning is None else (warning.get("source") or None),
        warning is not None,
        rule["scope"],
        rule["applies_to"],
        tuple(remedies),
        rule.get("chooser"),
    )


@dataclass(frozen=True)
class Counts:
    hits: int
    predicted: int
    expected: int

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(
            self.hits + other.hits,
            self.predicted + other.predicted,
            self.expected + other.expected,
        )

    @property
    def precision(self) -> float | None:
        return self.hits / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.hits / self.expected if self.expected else None


NONE = Counts(0, 0, 0)


def rules(predicted: Iterable[Mapping[str, Any]], expected: Iterable[Mapping[str, Any]]) -> Counts:
    """Rules paired one to one on equal keys."""
    got = Counter(rule_key(r) for r in predicted)
    want = Counter(rule_key(r) for r in expected)
    return Counts(sum((got & want).values()), got.total(), want.total())


def topics(predicted: Iterable[str], expected: Iterable[str]) -> Counts:
    """Needs-ruling topics as sets; "span_not_found" is the server's, not a label, so ignored."""
    got = set(predicted) - {"span_not_found"}
    want = set(expected)
    return Counts(len(got & want), len(got), len(want))


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round(p / 100 * len(ordered) + 0.4999))
    return ordered[min(rank, len(ordered)) - 1]
