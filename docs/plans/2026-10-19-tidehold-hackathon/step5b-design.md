# Step 5b design: deductions from the wording only

Plan step 5b (`../2026-10-19-tidehold-hackathon.md`); ADR `docs/adr/2026-10-23-balance-paid-before-trip-weather-refunds.md` D3; R20 (`requirements.md`). Written 2026-10-23 at 4545866, before building; reviewed at start and before commit (findings at the end).

No migration: rules are stored as JSON in `policy_versions.rules` and the spans go next to them. No deployment holds a version yet (step 10 has not run), so a stored deduction without a quote exists only in local development databases; it would fail `Rule.model_validate` when a trip binds it (recreate the version).

## Schema (`policy/schema.py`)

- `Deduction` gains `quote: Quote` (required, 1–600 characters, like the rule's and the warning's). `_DEDUCTION` in `WIRE_SCHEMA` gains `quote` (string, required: strict structured output needs every property).
- `RulingTopic` gains `deduction_conditional` (after `deduction_unspecified`), and so does the wire enum.
- The prompt hash changes (it covers `WIRE_SCHEMA`).

## Compile (`policy/compile.py`)

- `_locate_rule` locates every deduction's quote inside the rule's span (`spans.locate_within`; developer, 2026-10-23, review finding 1): a quote not found there loses the rule, which the retry names and, if still not found, becomes a `span_not_found` ruling. The retry note lists the quotes that were not found (before, it listed the rule quote even when only the warning quote was missing) and says a deduction's quote lies inside its rule's.
- `LocatedRule` gains `deduction_spans: list[list[Span]]` (per remedy, per deduction); `compilation_result` stores them as `deduction_spans` beside `span` and `warning_span`.

## Publish (`policy/versions.py`)

- `_check_rule` locates every deduction quote inside the rule's span and stores `deduction_spans` in the version's rule item, as above: `SPAN_NOT_FOUND` when the quote is not in the wording, `DEDUCTION_OUTSIDE_RULE` when it is only outside the rule's clause. Inside the rule, a ruling quoting the deduction overlaps the rule's span, so the unchanged settlement asks (`RULED_IN_TEXT`), and a short quote cannot resolve to an earlier, unrelated occurrence (review findings 1 and 2). A fee stated in its own paragraph cannot be a remedy's deduction; it is a ruling.
- Nothing else changes: answering every needs-ruling item (`RULING_UNANSWERED`) is what holds a `deduction_conditional` back until the operator rules.

## Prompt (`policy/prompt.py`)

- The deduction instruction: a fee the wording deducts from this remedy goes in `deductions`, a percent or a fixed amount, with its label and its own `quote`, copied exactly from inside the rule's quote; never a deduction the wording does not state.
- New needs-ruling item: `deduction_conditional`, a deduction that applies only under a condition a rule cannot state (a payment method: "for online payments", "if paid by card"), that contradicts a promised full refund (developer, 2026-10-23: this topic, not `conflicting_clauses`), or that a separate passage applies to refunds (follows from the inside-the-rule decision; not decided by the developer in words, reported); the ruling quotes the deduction, and a deduction inside the rule with a figure is also written in the remedy.
- The exclusion narrows from "… course failures or fees" to "… course failures, or fees charged when the guest cancels".

## Settlement

Unchanged (plan: evaluation is unchanged). A percent deduction is computed, a fixed one asks (`FIXED_DEDUCTION`), and a ruling whose quote overlaps the rule's span or its warning's asks (`RULED_IN_TEXT`). Scuba Tribe's deduction quote lies inside its rule's quote, so its `deduction_conditional` ruling overlaps the rule's span.

## Web (`web/src/features/policy/`)

- `types.ts`: a deduction has `quote: string`.
- `RemedyEditor.tsx`: each deduction shows its quote under its label and figure. The editor still cannot add a deduction by hand (as today); step 9's rule editor work is the plan's.

## Bench

- `labels.json`: Scuba Tribe's weather rule keeps refund 100% with the 3.5% deduction, now quoted "minus 3.5% processing fee for online payments", or credit 100%, chooser guest; its needs-ruling topics add `deduction_conditional`. Mike's Dauin adds `deduction_unspecified` and `deduction_conditional` ("All requests for refunds will be subject to a reasonable administrative fee depending on the payment method used": no figure, on the payment method; developer, 2026-10-23, review finding 3). Blue Ribbon's environmental and sanctuary fees (park or permit fees) and Wetzone's administration and transfer fees (charged on the guest's cancellation) stay excluded. Held-out labels were not read or changed. `version` becomes 2 (the benchmark records the labels version).
- `GUIDELINE.md`: rule 1 excludes fees charged when the guest cancels (named: date-change, transfer, administration) and park or permit fees, and says a fee deducted from the operator's refunds is not excluded; rule 6 gives a deduction its own quote inside the rule's and defines when it is `deduction_conditional`; the topic list gains it; scoring compares a deduction's kind and value, not its quote.
- `score.py` does not score quotes (unchanged).

## Tests

- `tests/integration/test_policy_compile.py::test_the_dev_fixtures_compile_to_their_labelled_rules_and_rulings` gains `scuba-tribe`. `FakeModel.answer` takes optional ruling quotes per topic.
- `tests/integration/test_policy_versions.py::test_a_deduction_quote_the_wording_does_not_contain_is_rejected` (two cases): a hand-written rule whose deduction quote is not in the wording gets `SPAN_NOT_FOUND`, one quoting another passage gets `DEDUCTION_OUTSIDE_RULE`; the same rule quoting its own fee publishes and stores the span of the occurrence inside the rule, not the earlier one.
- `tests/integration/test_policy_compile.py::test_a_conditional_deduction_needs_its_ruling_before_publish_and_its_settlement_asks` (in the compile suite, which has the fake model, rather than `test_settlement.py`, whose model raises; the fake's call count is asserted unchanged across the cancellation): the fake model compiles Scuba Tribe's wording with the `deduction_conditional` ruling quoting the deduction and the other ruling quoting nothing; publish without its answer is `RULING_UNANSWERED`; with it the version publishes; a trip on product "Fun Dives:" with a guest whose deposit is captured, cancelled whole for weather, gives a proposal `asked` with exactly `REMEDY_CHOICE` and `RULED_IN_TEXT`. Its control `test_without_a_quote_the_conditional_ruling_names_no_clause` runs the same with the ruling unquoted and gets `REMEDY_CHOICE` alone, so `RULED_IN_TEXT` comes from the stored spans, not the topic (review finding 5).
- `tests/unit/test_settlement_evaluate.py` and `tests/unit/test_policy.py`: deduction fixtures gain a quote (review finding 6).
- `tests/unit/test_policy.py`: `test_a_deduction_quote_outside_its_rules_quote_loses_the_rule` (retry naming the fee quote, then `span_not_found`); the offsets test also checks the deduction's span; `test_the_prompt_excludes_only_fees_charged_when_the_guest_cancels` (the "Do not ask about" sentence mentions fees only as charged when the guest cancels; the deduction instruction asks for a quote; `deduction_conditional` is listed).
- `tests/unit/test_model_choice.py::test_the_configured_model_and_prompt_are_the_last_benchmarked` fails until the Groq benchmark is rerun at the new prompt hash and appended (a human on the VM).
- `web/src/features/policy/RemedyEditor.test.tsx`: the quote is shown.
- Mutations: skip the deduction span check on publish (the plan's); read the deduction anywhere in the wording, on publish and on compile; store no span for a ruling; revert the prompt's exclusion. Each fails its test (`docs/evidence.md`).

## Docs

`docs/threat-model.md`'s amount row names the 5b tests (it named a test the plan does not list, `test_a_deduction_for_online_payments_becomes_a_ruling`); `docs/evidence.md` gets the mutation run.

## Review at start

An independent reviewer (a subagent that took no part in the design) read this design against the plan, the ADR, R20 and the code on 2026-10-23. 8 findings:

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | A deduction quoted outside the rule's clause gets a ruling that never overlaps the rule's span, so its percent is deducted without asking | HIGH | Developer chose: the deduction must lie inside its rule's clause, on compile and publish (`DEDUCTION_OUTSIDE_RULE`); evaluation unchanged |
| 2 | A `deduction_conditional` ruling with a null or unfound quote has no span, so answering it lets the deduction apply; a short quote can resolve to an earlier occurrence | HIGH | First-occurrence part fixed by locating inside the rule; developer chose no further publish check (a ruling's quote is not required to overlap a deduction). Recorded as a residual: a conditional ruling the model leaves unquoted settles at the rule's deduction once answered |
| 3 | The narrowed fee exclusion changes other dev labels (Mike's Dauin, Blue Ribbon, Wetzone) | MED | Developer chose: Mike's Dauin gains `deduction_unspecified` and `deduction_conditional`; the other two stay excluded (GUIDELINE rule 1 names why) |
| 4 | The order of commits around the benchmark rerun is not described | MED | Rejected: the developer asked for the code on a feature branch, stopped before the rerun, with the drift test failing; the human appends the run before merging (Progress row) |
| 5 | The settlement test does not isolate `RULED_IN_TEXT` (two remedies ask anyway) and names no mutation | MED | Changed: exact reasons, a control test with the ruling unquoted, other rulings' quotes null, mutation "store no span for a ruling" |
| 6 | Consumers missing: deduction fixtures in two unit suites, the 0008 migration's comment, the e2e server | MED | Changed: fixtures updated; the stored shape is documented in `versions.py` (migrations are not edited); the e2e fakes have no deductions |
| 7 | Nothing checks that a deduction's figure appears in its quote | LOW | Developer chose not to add it; recorded |
| 8 | Threat model row, `deduction_unspecified` with an unquoted ruling, the trip's product | LOW | Threat model row rewritten; the unquoted case is the residual of 2; the test binds "Fun Dives:" |

## Review before commit
A second independent reviewer read the committed implementation (2e2ebd9) on 2026-10-23. 7 findings:

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | A ruling's quote is still placed at its first occurrence (`locate`), so a short quote of the rule's fee can land in an earlier passage and a one-remedy rule settles with the deduction once the ruling is answered | HIGH | Fixed: a ruling's quote is placed inside a rule's clause or warning clause when it occurs there (`spans.locate_in_clauses`, on compile and publish), else at its first occurrence; `test_a_ruling_on_a_rules_fee_is_placed_in_that_rule_and_the_settlement_asks` (one remedy, inside the mandate: asked, not accepted; mutation: first occurrence) |
| 2 | The editor shows the deduction's quote as the model's text, not cut from the wording; the API does not cut deduction clauses | MED | Recorded for step 9, whose scope has "the rule editor shows each deduction's quote": the rule form already edits the rule's own quote as text and shows the cut clause beside it; cutting deduction clauses in the version and compile views goes with that screen |
| 3 | The retry note calls a fee quoted from another passage "not in the wording" and lists every deduction when only the rule's quote is missing | MED | Fixed: two lists, the second saying a deduction from another passage is not written in the remedy but asked as `deduction_conditional`; deductions listed as outside only when the rule's quote was found; the compile test asserts it (mutation fails it) |
| 4 | The Scuba Tribe settlement test cannot show a money change (two remedies ask anyway) | MED | Fixed by finding 1's one-remedy test |
| 5 | A deduction cannot be edited or removed in the web editor, so a narrowed rule quote meets `DEDUCTION_OUTSIDE_RULE` with no way out in the UI | LOW | Recorded for step 9 (rule editor); reported to the developer |
| 6 | `key={d.label}` collides on two deductions with the same label | LOW | Fixed: keyed by label and quote |
| 7 | The prompt never says a deduction from another passage stays out of the remedy | LOW | Fixed in the `deduction_conditional` item (prompt hash `76c549dec3d3f42d`) |
