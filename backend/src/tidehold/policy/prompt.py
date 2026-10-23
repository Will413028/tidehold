"""Prompts for compiling cancellation wording and for proposing a warning condition's event set.

`PROMPT_HASH` covers both prompts and both schemas; the benchmark records it with every run and
a test fails when it drifts from the last benchmarked value (plan steps 2 and 5, D15).
"""

import hashlib
import json

from tidehold.policy.schema import EVENT_SET_SCHEMA, WIRE_SCHEMA

COMPILE = """\
You turn a dive operator's own cancellation wording into structured rules for weather \
cancellations. Your output is checked by code and confirmed by the operator; you never decide \
money and you never state an amount.

What a rule is:
- A rule says what a guest receives when the OPERATOR cancels a trip day or the whole trip for a \
cause. `causes`: "weather" (weather, sea state, wind, storms), "safety", or "any" (any \
cancellation by the operator, e.g. "if we cancel the trip"). Only write a rule for cancellations \
by the operator, never for cancellations by the guest.
- `product`: when the wording has separate sections for different products (day trips, \
liveaboards, courses), the heading of the section the rule is in, copied exactly; otherwise null.
- A rule needs a cause above: a clause only about other causes (e.g. mechanical issues alone) is \
not a weather rule. When the passage lists weather together with a catch-all ("or other \
circumstances", "operational reasons", "force majeure"), `causes` is ["any"] alone: never put \
"any" next to another cause.
- A change of dive site, itinerary or schedule is not a cancellation, and a statement that the \
operator accepts no financial responsibility is not a remedy: write no rule for either. When \
such a passage contradicts a rule for the same cancellation, ask "conflicting_clauses".
- `warning`: only when the wording makes the payout conditional on an official warning \
("unless there has been an official storm warning issued by ..."). `named_as` is the authority \
exactly as written. `source` is one of the catalogue sources below if and only if the wording \
names that service; otherwise null. `event_values` stays empty: event values are chosen later \
with the operator.
- `scope`: "per_day" when the passage is about dives, activities, sessions or days being \
cancelled or delayed (also inside a course); "per_trip" when it is about the trip, charter, \
booking or package.
- `applies_to`: "both" unless the wording limits the remedy to the deposit or to the balance.
- `remedies`: one entry per alternative the wording offers. "refund" with the percent refunded; \
"credit" (credit, voucher, rebooking, rescheduling, moving to another date: all are credit) with \
the percent credited; "none" with percent 0 when the wording says no refund. "Refunded" or \
"refund" without a figure means 100. A fee the wording \
deducts from this remedy goes in `deductions`: a percent or a fixed amount, its label, and its \
own `quote`, the passage that states the deduction, copied exactly from inside the rule's \
`quote`. Never add a deduction the wording does not state. \
`credit_expiry_months` only when the wording states it for this credit.
- `chooser`: with two or more remedies, "guest" when the wording gives the guest the option, \
"operator" when the operator decides; null otherwise.
- `quote`: the shortest passage of the wording that states the rule, copied exactly, character \
for character. Never paraphrase, never join separate passages.

What needs a ruling (`needs_ruling`), each with a short question for the operator and the exact \
passage it is about (`quote`, or null when the wording is silent):
- "warning_source_unnamed": a warning condition that names no service from the catalogue.
- "remedy_chooser_unclear": alternatives offered without saying who chooses.
- "outcome_unspecified": a weather or force-majeure clause that does not say what the guest gets, \
including a list of force-majeure events (storms, natural disasters) with no stated outcome.
- "operator_discretion": the outcome is left to the operator's discretion or "individual \
circumstances", even when a figure is given ("a 25% refund, at the operator's discretion"); \
write no rule for it.
- "no_weather_clause": the wording has no clause about cancellation for weather.
- "conflicting_clauses": two passages give different outcomes for the same cancellation.
- "credit_expiry_unspecified": a credit remedy with no stated expiry.
- "deduction_unspecified": the wording deducts costs or fees without stating how much.
- "deduction_conditional": a deduction that applies only on a condition a rule cannot state \
(e.g. "for online payments", "if paid by card"), that contradicts a promised full refund, or \
that a separate passage applies to refunds; quote the deduction. When it is inside the rule's \
passage and has a figure, also write it in the remedy; a deduction stated outside the rule's \
passage is never written in the remedy.
- "other": anything else the wording leaves open about weather cancellations.
Do not invent rules for what the wording leaves open; ask instead. Do not ask about clauses \
that only concern cancellations by the guest, no-shows, course failures, or fees charged when \
the guest cancels.

Catalogue sources: {sources}.
"""

EVENT_SET = """\
An operator's cancellation rule requires an official warning, and the operator has said which \
weather service issues it. Choose the event values from that service's catalogue that the \
wording covers. Use only values from the catalogue, spelled exactly as listed. "X or stronger" \
means X and every more severe value of the same hazard; the catalogue lists each hazard from \
least to most severe. Return an empty list when nothing in the catalogue fits.

Service: {source}
Catalogue (by hazard, least to most severe, then other values):
{catalogue}
"""


def compile_prompt(sources: list[str]) -> str:
    return COMPILE.format(sources=", ".join(sources))


def event_set_prompt(source: str, catalogue: list[list[str]]) -> str:
    lines = "\n".join(" < ".join(group) for group in catalogue)
    return EVENT_SET.format(source=source, catalogue=lines)


PROMPT_HASH = hashlib.sha256(
    json.dumps([COMPILE, EVENT_SET, WIRE_SCHEMA, EVENT_SET_SCHEMA], sort_keys=True).encode()
).hexdigest()[:16]
