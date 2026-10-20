# Labelling guideline

How the expected outputs in `labels.json` are written. Fixed before labelling (plan step 2, D16); a change to it changes the labels in the same commit. The compile prompt (`backend/src/tidehold/policy/prompt.py`) states the same rules to the model.

## Input

Each fixture in `fixtures/` is the cancellation section an operator would paste, cut from its public page with `excerpt.py` (URL, access date, line ranges and the excerpt's sha256 in the file). The full pages are not committed.

## Rules

A rule says what a guest gets when the **operator** cancels, or accepts a cancellation of, a trip day or the whole trip.

1. **Only operator cancellations.** Guest cancellations, no-shows, late arrivals, course failures, date-change fees and park or permit fees produce no rule and no ruling.
   A change of dive site, itinerary or schedule is not a cancellation, and a disclaimer of financial responsibility is not a remedy: neither is a rule; when one contradicts a rule for the same cancellation, it is a `conflicting_clauses` ruling.
2. **Causes** are `weather` (weather, sea state, wind, storms, conditions), `safety` (safety concerns, unsafe conditions) or `any` (any operator cancellation). A list that includes a catch-all ("other circumstances", "operational reasons", "force majeure", "mechanical issues" next to weather) is `any` alone. A clause about other causes only (mechanical issues alone) is not a rule.
3. **Warning condition** only when the payout depends on official information ("unless there has been an official storm warning issued by …", a park closure decided by the authorities). `named_as` is the authority as written; `source` is null unless the wording names PAGASA, NWS or MeteoAlarm. Event values are never labelled at compile time.
4. **Scope** `per_day` when the wording speaks of a dive, an activity, a session or a day being cancelled or delayed (also inside a course); `per_trip` for a trip, charter, booking or package.
5. **Applies to** `both` unless the wording limits it to the deposit or the balance.
6. **Remedies**, one per alternative: `refund` or `credit` with the percent; rescheduling, rebooking, a date change or a voucher is `credit`; "refunded" without a figure is 100; "no refund" is `none` 0. A deduction with a stated figure is a deduction; one without a figure is a `deduction_unspecified` ruling and the remedy keeps its percent. A stated credit validity goes in `credit_expiry_months`.
7. **Chooser** with two or more remedies: `guest` when the guest is given the choice ("guests may choose", "you'll receive … or the option to"), `operator` when the operator decides; otherwise null and a `remedy_chooser_unclear` ruling.
8. **Product**: the section heading when the wording has separate sections per product; null otherwise. Not scored.
9. One rule per passage; never join separate passages into one rule.

## Needs ruling

Scored as the set of topics per fixture (one topic counts once however many passages raise it).

- `warning_source_unnamed`: a warning condition naming no catalogue source.
- `remedy_chooser_unclear`: alternatives with no chooser.
- `outcome_unspecified`: a weather or force-majeure clause, or a cancellation the operator accepts, with no stated outcome.
- `operator_discretion`: the outcome is left to the operator's discretion, even with a figure attached; no rule is written for it.
- `no_weather_clause`: nothing about cancellations for weather.
- `conflicting_clauses`: two passages give different outcomes for the same cancellation.
- `credit_expiry_unspecified`: a credit remedy without a stated expiry.
- `deduction_unspecified`: costs or fees deducted without a figure.

## Scoring

- A predicted rule matches a labelled rule when causes (as a set), warning presence and source, scope, applies to, remedies (kind, percent, deductions) with credit expiry, and chooser are all equal; rules are paired one to one. Rule precision and recall follow.
- Needs-ruling precision and recall over the topic sets.
- Event sets: exact match of the proposed set.

## Splits

`dev`: Pure Dive, Blue Ribbon, Mike's Dauin, Scuba Tribe, Wetzone. The prompt may be tuned on these; step 5's acceptance uses them. `held_out`: Kaia, Dive Komodo, Dragon Dive, Aquatic Apes. Nobody tunes the prompt while reading their results; a change to the prompt after a held-out run makes that run stale.
