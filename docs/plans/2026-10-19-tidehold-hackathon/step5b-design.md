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

## Fix round after the developer's local acceptance (2026-10-23)

The developer's independent reviewer found 2 MED and 1 LOW: (1) a `deduction_conditional` ruling with a null quote, or quoting the same fee elsewhere, never overlaps the rule, so once answered the settlement deducts inside the mandate without asking; (2) a ruling quote that occurs in two products' clauses covers only the first, so a trip on the second product does not ask; (3) a quote whose first occurrence straddles rule A's boundary and whose second lies inside rule B leaves A unasked. Developer's decisions (2026-10-23): the check is structural and on publish; `deduction_unspecified` (no figure, so no `Deduction`) must overlap a rule's clause; a deduction ruling quoting a passage outside every rule is refused on publish and invalid on compile, and Mike's Dauin's label goes back to `no_weather_clause` alone.

- Every occurrence counts. `spans.locate_all` returns every occurrence of a quote in the wording (folded as `locate` folds). A stored ruling keeps `span` (the occurrence shown: inside a rule's clause when there is one) and gains `spans` (every occurrence). `settlement/proposals.py` reads `spans` (falling back to `[span]` for a ruling stored without it) when it asks whether a free-text ruling covers a rule; `evaluate.ruled_in_text` is unchanged. Chosen over matching text at evaluation: the offsets are computed once, on publish, by the same locator, and the settlement keeps reading spans only (the smallest change that makes every covered rule ask). This fixes (2) and (3): every occurrence that overlaps a rule's clause marks that rule.
- Publish (`versions.check`): a `deduction_conditional` ruling must have a quote one of whose occurrences overlaps a stored deduction span; a `deduction_unspecified` ruling must have a quote one of whose occurrences overlaps a rule's clause (or its warning clause). Otherwise `Invalid("DEDUCTION_RULING_OFF_RULE")`. A conditional deduction without a figure is asked as `deduction_unspecified` (it has no `Deduction` to overlap).
- Compile: the same test on the kept rules; an unplaced ruling alone triggers the retry. A deduction ruling that fails it is named in the retry note (with lost rules' quotes); after the last attempt it is replaced by a `span_not_found` item ("a question about a deduction the compiler could not tie to a rule's clause"), as a lost rule is. On publish a `span_not_found` item covers every clause and deduction of the submitted rules (`spans`), so an unquoted conditional or unspecified question still makes every rule ask (developer, 2026-10-23, start review finding 1 and closing review finding 1). A `span_not_found` for a lost rule covers them too: more asks, never fewer.
- No escape needed when nothing is deducted: the `deduction_conditional` check is skipped when no submitted rule has a deduction (developer, 2026-10-23, start review finding 3), so an operator who removes the fee can still publish from the compilation.
- Prompt and GUIDELINE: a deduction ruling quotes the deduction inside the rule's passage; a fee stated only in a separate passage is neither written in a remedy nor asked about (ADR D3: without a deduction in the rule the guest gets the full amount). The `deduction_conditional` definition drops "applied by a separate passage". Mike's Dauin: `no_weather_clause` only (labels version 3). The retry note for a deduction quoted outside its rule says to remove it from the remedy.
- Threat model: the residual "a ruling the model leaves unquoted names no clause" is removed.
- `spans` is stored for every topic, so a warning, discretion or expiry ruling whose quote recurs marks every rule it occurs in: more asks, never fewer; the API's version view returns it (the web does not read ruling spans). A compilation stored before this round can now fail publish in a development database; recreate it.
- Tests: publish refuses a `deduction_conditional` ruling with a null quote and one quoting the fee outside the rule (`DEDUCTION_RULING_OFF_RULE`), and a `deduction_unspecified` ruling off every rule; a valid one settles asked with `RULED_IN_TEXT` (existing one-remedy test); two products whose clauses carry the same conditional fee sentence, one ruling, a trip on each product asks with `RULED_IN_TEXT`; the straddle case (first occurrence crossing rule A's clause boundary, second inside rule B) asks on both; compile replaces an off-rule deduction ruling with `span_not_found` after the retry. Mutations: drop the overlap check (publish test fails); keep only the first occurrence in `spans` (two-product test fails).
- The prompt hash changes again.

### Review at start of the fix round

An independent reviewer read this section against the plan, the ADR and the code on 2026-10-23. 9 findings:

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | The compile fallback reopens the hole: a null-quoted conditional ruling becomes an unquoted `span_not_found`, the deducting rule is kept, and a one-remedy rule settles with the deduction | HIGH | Developer chose: the replacement covers every deduction; `test_an_unquoted_conditional_ruling_is_replaced_by_one_covering_every_deduction` |
| 2 | The fixture test and the unquoted control contradict the new check | MED | Fixed: the fixture test quotes Scuba Tribe's fee; the control is replaced by finding 1's test; threat model updated |
| 3 | No way to publish from a compilation after removing the fee | MED | Developer chose: skip the conditional check when no rule deducts; `test_a_conditional_ruling_with_nothing_deducted_is_not_checked` |
| 4 | No test separates the conditional (deduction) target from the clause target | MED | Fixed: a conditional ruling quoting the rule's refund sentence is refused (mutation fails it) |
| 5 | One conditional ruling quoting rule A's fee leaves a different fee in rule B unasked | LOW | Recorded as a residual: the compiler raises one ruling per deduction it finds conditional; requiring each deducting rule to be covered would refuse an unconditional fee in B |
| 6 | `spans` applies to every topic | LOW | Kept (asks more, never fewer), stated above |
| 7 | `locate_all` unspecified (overlap, folding, offsets) | LOW | Built to advance one folded character and map back through `origin`; unit test with overlapping and typographic occurrences |
| 8 | An unplaced ruling alone must trigger the retry | LOW | Built and tested (`test_a_deduction_ruling_off_every_rule_alone_is_retried_then_replaced`) |
| 9 | No migration; old compilations may fail publish in dev databases | — | Noted above |

### Review before commit of the fix round

An independent reviewer read the uncommitted fix round on 2026-10-23. 0 HIGH, 1 MED, 5 LOW:

| # | Finding | Severity | Outcome |
|---|---|---|---|
| 1 | The replacement for an unplaced `deduction_unspecified` covers deductions only, and an unspecified fee has none, so a one-remedy rule refunds the full amount without asking | MED | Developer chose: every `span_not_found` item covers every clause and deduction; `test_an_unquoted_unspecified_deduction_ruling_makes_every_rule_ask` (mutation: deductions only) |
| 2 | Publish refuses an operator's edit that removes a conditional fee while another rule keeps a fee, or that shortens the quoted rule | LOW | Recorded: the escapes are recompiling or publishing by hand; the check is skipped only when no rule deducts (developer's decision); the editor's deduction controls are step 9's |
| 3 | The threat model still says a fee in a separate passage becomes a ruling | LOW | Fixed |
| 4 | A retry-test assertion is vacuous; no test of a ruling placed on the retry | LOW | Fixed: the assertion reads the retry note; `test_a_deduction_ruling_placed_on_the_retry_is_kept` |
| 5 | `ruled_in_text`'s docstring predates `spans` | LOW | Fixed |
| 6 | A deduction question filed under another topic escapes the check | LOW | Recorded as a residual in the threat model (the deduction itself is verbatim with its own span) |

