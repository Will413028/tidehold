"""Locate a quoted span in the operator's stored wording (D16, plan step 2).

The model returns a quote; the server finds it in the wording and keeps the offsets, and every
screen renders the clause from the stored wording by those offsets, never the model's text.
Matching folds only what copying text changes without changing its meaning: Unicode
compatibility forms (NFKC), runs of whitespace, and typographic quotes and dashes. Anything
else, a paraphrase or a dropped word included, is not found.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

# Typographic marks a model or a copy-paste turns into ASCII (and the reverse).
_FOLD = str.maketrans(
    {
        **dict.fromkeys((0x2018, 0x2019, 0x201A, 0x2032), "'"),  # single quotes, prime
        **dict.fromkeys((0x201C, 0x201D, 0x201E, 0x2033), '"'),  # double quotes, double prime
        **dict.fromkeys((0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212), "-"),  # dashes, minus
    }
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int


def _folded(text: str) -> tuple[str, list[int]]:
    """The folded text and, for each of its characters, the index in `text` it came from."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for folded in unicodedata.normalize("NFKC", char).translate(_FOLD):
            if folded.isspace():
                if chars and chars[-1] == " ":
                    continue
                folded = " "
            chars.append(folded)
            origin.append(index)
    return "".join(chars), origin


def locate(quote: str, wording: str) -> Span | None:
    """Where `quote` occurs in `wording`, as offsets into `wording`; None when it does not.
    An ambiguous quote (more than one occurrence) resolves to the first."""
    needle, _ = _folded(quote)
    needle = needle.strip()
    if not needle:
        return None
    haystack, origin = _folded(wording)
    at = haystack.find(needle)
    if at < 0:
        return None
    last = at + len(needle) - 1
    return Span(origin[at], origin[last] + 1)


def locate_all(quote: str, wording: str) -> list[Span]:
    """Every occurrence of `quote` in `wording` (overlapping ones included), first to last: a
    ruling's quote covers every clause it occurs in (step 5b)."""
    needle, _ = _folded(quote)
    needle = needle.strip()
    if not needle:
        return []
    haystack, origin = _folded(wording)
    found: list[Span] = []
    at = haystack.find(needle)
    while at >= 0:
        found.append(Span(origin[at], origin[at + len(needle) - 1] + 1))
        at = haystack.find(needle, at + 1)
    return found


def overlaps(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def locate_within(quote: str, wording: str, within: Span) -> Span | None:
    """Where `quote` occurs inside the `within` part of `wording`, as offsets into `wording`;
    None when it does not occur there (a deduction is read inside its rule's clause)."""
    found = locate(quote, wording[within.start : within.end])
    return None if found is None else Span(within.start + found.start, within.start + found.end)


def locate_in_clauses(quote: str, wording: str, clauses: Sequence[Span]) -> Span | None:
    """Like `locate`, but an occurrence inside one of `clauses` wins over an earlier one
    elsewhere: a ruling quoting a rule's fee is placed inside that rule's clause, so the
    settlement sees the ruling on the rule (step 5b)."""
    for clause in clauses:
        found = locate_within(quote, wording, clause)
        if found is not None:
            return found
    return locate(quote, wording)
