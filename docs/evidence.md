# Evidence

Measurements, coverage reports and sandbox runs that plan steps cite in their acceptance. Newest first within each step.

## Step 5: policy compilation and rulings

### Live compiles on the VM (2026-10-20 00:21 to 00:51 UTC)

The compile benchmark at prompt `9f7c6e9404b56080` (`docs/plans/2026-10-19-tidehold-hackathon/model-benchmark.md`, newest run) ran the product's compile path (`tidehold.policy.compile_policy`) on oci-a1 with the D13 key file. `groq:openai/gpt-oss-120b`, the default `COMPILE_MODEL`, completed 17 of 18 compiles with live provider calls; the slowest call took 6.7 s and p95 was 6.7 s, against a limit of 60 s. The one failure was a Groq HTTP 400 `json_validate_failed`; in the product that call falls back to the no-AI path (`test_model_failure_falls_back_to_no_ai`). These compiles did not go through the HTTP route and its `model_calls` cap: a VM deploy is step 10, and the route is tested against a fake model. After the run the fallback demo answered 200.

### Mutations (2026-10-20, local)

Each mutation below made at least one test fail; the code was restored afterwards.

| Mutation | Test that failed |
|---|---|
| Skip span verification when publishing | `test_a_fabricated_span_is_rejected`, `test_a_warning_condition_quote_must_be_found_too` |
| Never reach the daily cap | `test_daily_cap_replays_recorded_results_then_stops_live_calls` |
| Let a model failure through instead of falling back | `test_model_failure_falls_back_to_no_ai`, `test_a_failed_live_call_replays_a_recorded_result_for_the_same_input` |
| Drop Groq's output-token cap | `test_every_provider_call_caps_its_output_tokens[groq]` |
| Change one line of the compile prompt | `test_the_configured_model_and_prompt_are_the_last_benchmarked` |

## Step 5b: deductions from the wording

### Mutations (2026-10-23, cloud container)

Each change applied alone to the step 5b tree, the named tests run, the change reverted.

| Mutation | Fails |
|---|---|
| Skip the deduction span check on publish (`_deduction_span` returns a span without locating) | `test_a_deduction_quote_the_wording_does_not_contain_is_rejected` (both cases) |
| Publish reads a deduction quote anywhere in the wording, not inside its rule's | `test_a_deduction_quote_the_wording_does_not_contain_is_rejected[…DEDUCTION_OUTSIDE_RULE]` |
| Compile reads a deduction quote anywhere in the wording | `test_a_deduction_quote_outside_its_rules_quote_loses_the_rule` |
| Publish stores no span for a ruling | `test_a_conditional_deduction_needs_its_ruling_before_publish_and_its_settlement_asks` |
| The prompt excludes "fees" again ("… course failures or fees.") | `test_the_prompt_excludes_only_fees_charged_when_the_guest_cancels` |
| Publish places a ruling's quote at its first occurrence, not inside a rule's clause (closing review) | `test_a_ruling_on_a_rules_fee_is_placed_in_that_rule_and_the_settlement_asks` |
| The retry note lists a fee quoted from another passage as "not in the wording" (closing review) | `test_a_deduction_quote_outside_its_rules_quote_loses_the_rule` |

Fix round after the developer's local acceptance (2026-10-23, cloud container; each against `tests/unit/test_policy.py` and `tests/integration/test_policy_compile.py`):

| Mutation | Fails |
|---|---|
| Publish skips the deduction ruling check | `test_a_deduction_ruling_off_its_rule_is_refused_on_publish` (all 5 cases) |
| A ruling covers only its first occurrence | `test_a_ruling_on_a_rules_fee_is_placed_in_that_rule_and_the_settlement_asks`, `test_one_ruling_on_a_fee_sentence_two_products_share_asks_on_each[Liveaboards:]`, `test_a_ruling_quote_straddling_one_clause_and_inside_another_asks_on_both[Liveaboards:]` |
| A ruling covers only the occurrence shown (the behaviour before the fix) | `test_one_ruling_on_a_fee_sentence_two_products_share_asks_on_each[Liveaboards:]`, `test_a_ruling_quote_straddling_one_clause_and_inside_another_asks_on_both[Day trips:]`, and 3 more |
| A conditional ruling is checked against the rules' clauses, not their deductions | `test_a_deduction_ruling_off_its_rule_is_refused_on_publish[ruling1]` |
| A `span_not_found` item covers nothing | `test_a_span_not_found_item_covers_every_clause_and_deduction`, `test_an_unquoted_conditional_ruling_is_replaced_by_one_covering_every_deduction`, `test_an_unquoted_unspecified_deduction_ruling_makes_every_rule_ask` |
| A `span_not_found` item covers deductions only (closing review) | `test_a_span_not_found_item_covers_every_clause_and_deduction`, `test_an_unquoted_unspecified_deduction_ruling_makes_every_rule_ask` |
| Compile does not retry when only a ruling is unplaced | `test_a_deduction_ruling_off_every_rule_alone_is_retried_then_replaced`, `test_an_unquoted_conditional_ruling_is_replaced_by_one_covering_every_deduction` |

Prompt hash after the fix round: `00bdb5c3b65fa352`; after the first 5b commits it was `76c549dec3d3f42d` (was `9f7c6e9404b56080`). The Groq benchmark at the new prompt hash has not run (a human on the VM); until it is appended to `model-benchmark.md`, `test_the_configured_model_and_prompt_are_the_last_benchmarked` fails.

## Step 5c: the Claude provider

### Mutations (2026-10-23, cloud container)

Each against `tests/unit/test_anthropic.py` and `tests/unit/test_model_choice.py`, reverted after; no real API call.

| Mutation | Fails |
|---|---|
| Drop `max_retries=0` | `test_each_failure_is_a_provider_error_of_its_kind_sent_once` (the 429s, the 500, the 529, the timeout: more than one request) |
| Treat a refusal as success | `test_each_failure_is_a_provider_error_of_its_kind_sent_once` (the refusal case) |
| The spend cap counts as a per-minute limit | `test_each_failure_is_a_provider_error_of_its_kind_sent_once` (the spend-cap case) |
| A billing error is not spent | `test_each_failure_is_a_provider_error_of_its_kind_sent_once` (the 402 case) |
| A non-JSON answer escapes | `test_each_failure_is_a_provider_error_of_its_kind_sent_once` (the non-JSON case) |
| An answer without text is a success (closing review) | `test_each_failure_is_a_provider_error_of_its_kind_sent_once` (the no-content case) |
| Send the schema unconverted | `test_the_request_names_the_exact_model_its_format_and_effort_and_no_sampling` |
| Drop the base URL pin | `test_no_environment_variable_points_the_sdk_elsewhere` |
| Claude's output cap set to the others' | `test_the_request_names_the_exact_model_its_format_and_effort_and_no_sampling`, `test_every_provider_call_caps_its_output_tokens[anthropic]` |

The Claude benchmark has not run (the developer's key, a human step).

## Step 7: official warnings and corroboration

### Recorded feeds read into the evidence store (2026-10-19 14:00 UTC)

All 927 recorder files from `oci-a1:/var/lib/tidehold-recorder` (2026-10-18 17:51 to 2026-10-19 13:30 UTC) through `python -m tidehold.bootstrap.ingest_warnings` into a local PostgreSQL 18: `{"new": 1071, "seen": 11013, "unreadable": 0}` in 20 s. Warnings by source and event (first fetch, last expiry, UTC):

| Source | Event | Count | First fetch | Last expiry |
|---|---|---|---|---|
| NWS | Small Craft Advisory | 656 | 10-18 17:52 | 10-24 10:00 |
| NWS | Gale Warning | 271 | 10-18 17:52 | 10-22 10:00 |
| NWS | Gale Watch | 20 | 10-18 17:52 | 10-22 00:00 |
| NWS | Special Marine Warning / Hazardous Seas Warning / Storm Watch / Storm Warning / other | 18 | 10-18 17:52 | 10-21 01:00 |
| MeteoAlarm (GR, HR, MT) | Yellow wind warning | 23 | 10-18 17:52 | 10-21 13:59 |
| MeteoAlarm | Yellow fog / Moderate thunderstorm / Moderate rain warning | 29 | 10-18 17:52 | 10-20 06:59 |
| PAGASA | General Flood Advisory (Moderate / Final) | 54 | 10-18 17:51 | 10-19 21:43 |

Read: NWS gale and storm warnings and MeteoAlarm wind warnings are recorded triggers for a demo rule; PAGASA has still carried only flood advisories (D4: JMA replaces PAGASA if no wind or tropical-cyclone warning is recorded by 2026-10-30).

### Open-Meteo corroboration, one live call (2026-10-19 14:05 UTC)

`openmeteo.fetch` for Anilao (13.7565, 120.9137), 2026-10-19 to 10-21: daily `wave_height_max` 0.26, 0.14 and 0.80 m (`https://marine-api.open-meteo.com/v1/marine?latitude=13.75650&longitude=120.91370&daily=wave_height_max&timezone=UTC&start_date=2026-10-19&end_date=2026-10-21`).

## Step 6: trips, guests and the balance payment

### Coverage after the D18 rewrite (2026-10-23, report only)

`make coverage` on `feat/step6-balance-before-trip` (270 backend tests, PostgreSQL 18 in a container; run in the implementing cloud session, to be rerun locally), money, holds and settlement:

```
Name                                   Stmts   Miss Branch BrPart  Cover
src/tidehold/holds/__init__.py             0      0      0      0   100%
src/tidehold/holds/http.py                86      0      8      0   100%
src/tidehold/holds/schedule.py             8      0      0      0   100%
src/tidehold/money/__init__.py             0      0      0      0   100%
src/tidehold/money/amounts.py              3      0      0      0   100%
src/tidehold/money/commands.py           155     11     54     10    90%
src/tidehold/money/paypal.py             168      5     20      3    96%
src/tidehold/money/service.py            244     46     92     13    79%
src/tidehold/settlement/__init__.py        0      0      0      0   100%
src/tidehold/settlement/drafts.py         43      3      6      1    92%
src/tidehold/settlement/evaluate.py      151      0     44      2    99%
src/tidehold/settlement/execute.py       159      7     72      7    94%
src/tidehold/settlement/guest.py          73      1     20      1    98%
src/tidehold/settlement/http.py          200      1     12      2    99%
src/tidehold/settlement/matching.py       20      0      2      0   100%
src/tidehold/settlement/plan.py           35      2     12      3    89%
src/tidehold/settlement/proposals.py     195     13     66     11    90%
src/tidehold/settlement/watch.py          73      5     24      5    90%
TOTAL                                   1613     94    432     58    92%
```

Read: `holds` is fully covered, including a guest without a balance payment (invited before migration 0015; `NO_BALANCE_PAYMENT`, added after the closing review). `money` and `settlement` are unchanged by this step.

### Mutation of the D18 rewrite (2026-10-23, cloud session)

| Mutation | Tests that failed |
|---|---|
| Due date counted from the last trip day (`starts_on + days - 1`) | `test_the_balance_falls_due_its_days_before_the_first_local_trip_day` (all three cases), `test_a_zero_day_balance_falls_due_when_the_first_local_trip_day_begins`, `test_a_guest_gets_its_balance_payment_at_invitation` |

### Browser check of the PayPal checkouts (2026-10-24)

Run on `3c9a05a` plus the token fix below, on the local stack: backend on PostgreSQL 18 in Docker with the sandbox app's credentials, web as a production build (`pnpm build && pnpm start`; `next dev` refuses the 127.0.0.1 origin's dev resources, so the page never hydrates), opened in Chrome at `http://127.0.0.1:3000` with `PUBLIC_ORIGIN` matching. The policy was compiled through Claude Sonnet 5.5 and published; trip "Checkout check" (2026-12-20, 2 days, NWS PKZ012, deposit USD 50, balance USD 300 due 2026-11-20), one guest.

- PayPal's JS SDK rendered "Pay with PayPal" and "Debit or Credit Card" for "Pay the deposit"; "Pay with PayPal" opened PayPal's checkout window (not completed: a sandbox buyer login is the developer's to enter).
- Deposit, then balance, through PayPal's own card form ("Debit or Credit Card", guest checkout, fictional billing details, Fastlane not saved) with PayPal's published sandbox Visa 4012 0000 7777 7777: the page showed "Deposit: USD 50.00: paid", then "Balance: USD 300.00: paid"; both payments `captured` (5000 and 30000 minor units).
- The trip's timeline listed `deposit · open_succeeded` (03:17:19 UTC) and `balance · open_succeeded` (03:18:26 UTC).
- PayPal's card form declined 4111 1111 1111 1111 (the server-side `TEST_CARD`, which the API accepts); that number is not on PayPal's published list for card fields.
- Found: after the Mac slept, every order creation was refused (`open_rejected`, `PAYPAL_REJECTED` with no order): the client timed its cached access token by `time.monotonic()`, which macOS stops during sleep, so it kept sending a token PayPal had expired. Fixed in the same push: the expiry runs on the wall clock, and a 401 replaces the token and sends the request once more under the same `PayPal-Request-Id` (`test_a_token_paypal_no_longer_accepts_is_replaced_and_the_request_sent_once_more`, `test_a_token_expires_by_the_wall_clock_so_a_sleeping_host_does_not_reuse_it`; mutations: no resend, monotonic clock, keep the stale token: each fails its test).

## Step 4: money engine port

### Coverage after the D18 rewrite (2026-10-23, report only)

`make coverage` on `feat/step4-capture-refunds` (253 backend tests, PostgreSQL 18 in a local container), money and settlement:

```
Name                                   Stmts   Miss Branch BrPart  Cover   Missing
----------------------------------------------------------------------------------
src/tidehold/money/commands.py           137     10     50      9    90%   83, 143, 183, 205, 232, 236, 261, 366, 412-413
src/tidehold/money/paypal.py             168      5     20      3    96%   97, 108, 113, 168-169, 221->223
src/tidehold/money/service.py            218     47     76     13    76%   57, 67-68, 78, 93, 109, 128, 136, 140-141, 155, 205-208, 211-213, 218-228, 235, 247, 249-250, 272-273, 299, 321, 329-330, 339, 386-389, 404-413
src/tidehold/settlement/execute.py       126      6     54      6    93%   97, 175, 179, 212, 249, 299
src/tidehold/settlement/plan.py           35      2     12      3    89%   37, 42, 51->exit
TOTAL (money, holds, settlement)        1540     94    398     57    92%
```

Read: the uncovered reconciliation lines are the order reads that end other than in a completed capture (a 404, a failed read, an order placed but not as recorded, an approved checkout resent, a `CREATED`/`VOIDED` order rejected; `service.py` 205-228) and a refund read that fails or finds no order (247-250); the test-card attempt without a card and the checkout paths are the ones step 4 measured before.

### Mutations of the D18 rewrite (2026-10-23, local)

Each made the named test fail; the code was restored afterwards.

| Mutation | Test that failed |
|---|---|
| Refund window 181 days instead of 180 | `test_a_refund_past_the_window_is_refused_before_paypal_is_called` |
| Per-proposal refund index made non-unique | `test_a_proposal_refunds_a_payment_once_when_settled_twice[50]`, `[100]` |
| Open refunds left out of the refund bound | `test_two_concurrent_refunds_never_exceed_the_capture` |
| Payment row not locked when recording a refund | `test_two_concurrent_refunds_never_exceed_the_capture` |
| Resend an unknown open without reading its order | `test_an_unknown_open_is_resolved_from_its_order_before_any_resend` |
| A run that finds its settlement's refund already recorded raises instead of stepping aside | `test_a_proposal_refunds_a_payment_once_when_settled_twice[50]`, `[100]` |
| No reconcile scheduled for a settlement command already sent | `test_a_settlement_refund_sent_before_a_crash_is_read_and_finished` |
| A refund refused when recorded (window, bound) is not caught by the settlement (local acceptance review) | `test_a_refund_refused_when_recorded_asks_and_the_other_guests_are_refunded` (the later guest got no refund) |
| A refund PayPal reports PENDING backs off with `RetryLater` instead of waiting (local acceptance review) | `test_a_pending_refund_is_read_until_paypal_completes_it` (the settlement never ended) |
| No window check before a refund's first send (local acceptance review) | `test_a_refund_whose_window_closes_before_its_first_send_is_not_sent[settlement]`, `[reconciliation]` |
| A wait does not reset the failures in a row (closing review) | `test_waiting_is_not_a_failure_and_starts_the_failures_in_a_row_over` |
| A lease taken over is not counted as a failure (closing review) | `test_a_lease_taken_over_counts_as_a_failure`, `test_a_worker_that_lost_its_lease_cannot_make_the_action_wait` |
| `wait` without the lease's fencing token (closing review) | `test_a_worker_that_lost_its_lease_cannot_make_the_action_wait` |
| `retry` counts claims instead of failures (closing review) | `test_waiting_is_not_a_failure_and_starts_the_failures_in_a_row_over` |
| Any read result counts as pending (closing review) | `test_a_refund_read_back_with_another_amount_is_not_waited_on` |
| A settlement with a pending refund completes (`return`) instead of `Waiting` (second independent review) | `test_a_settlement_waiting_on_a_pending_refund_asks_when_paypal_fails_it` (the settlement's action was `done`, so the dropped wake left nothing to run it) |
| A command held for the operator (resend refused) counts toward `RetryLater` (second independent review) | `test_a_pending_refund_beside_a_refused_resend_is_read_until_it_ends` (the action failed at the 8th run while the refund was still PENDING) |
| Reconciliation claims a never-sent command without `first_send` (second independent review) | `test_reconciliation_leaves_a_refund_a_settlement_just_sent_to_a_read` (2 refund POSTs instead of 1) |
| A settlement does not schedule a read when the last one ended (its design review) | `test_a_settlement_reads_a_refund_whose_read_was_dropped` (the settlement stayed `accepted`) |
| A settlement keeps waiting after the refund's reads gave up (its design review) | `test_a_settlement_whose_pending_refund_is_no_longer_read_fails` (the settlement waited on a refund nobody reads) |
| "The reads gave up" decided per payment, not per command (`read_at` ignored; its closing review) | `test_a_settlement_reads_a_refund_whose_read_was_dropped[failed]` (the settlement stayed `accepted`) |
| The settlement looks at the oldest `reconcile_payment` of the payment, not the latest (its closing review) | `test_an_older_failed_read_does_not_stop_a_settlement_whose_refund_is_read` (`RetryLater: PAYPAL_REFUND_NOT_READ`) |
| Reconciliation does not stamp `read_at` (its closing review) | `test_a_settlement_whose_pending_refund_is_no_longer_read_fails` (the settlement waited on a refund nobody reads) |

### Coverage of the money path (2026-10-19, report only)

`make coverage` at the step-4 commits (53 backend tests, local PostgreSQL 18 container):

```
Name                                  Stmts   Miss Branch BrPart  Cover   Missing
---------------------------------------------------------------------------------
src/tidehold/money/__init__.py            0      0      0      0   100%
src/tidehold/money/commands.py          111     10     38      9    87%   65, 79, 114, 143, 183, 206, 210, 267, 307-308
src/tidehold/money/paypal.py            149      9     16      3    93%   86, 97, 102, 157-158, 173->175, 208, 266, 269, 272
src/tidehold/money/service.py           116     22     40      8    78%   38, 53, 57, 85, 93, 100, 102-103, 112, 138-139, 187-190, 205-214
src/tidehold/settlement/__init__.py       0      0      0      0   100%
src/tidehold/settlement/plan.py          35      2     12      3    89%   37, 42, 51->exit
---------------------------------------------------------------------------------
TOTAL                                   411     43    106     23    86%
```

Read: the uncovered service lines are a checkout order refused at creation (187-190), a checkout attempt closed by a concurrent reconcile (205-214) and a reconcile that skips a test-card command without a card (138-139); `holds` has no code yet.

### Sandbox smoke after the D18 rewrite (2026-10-23 12:23 UTC, PayPal sandbox)

On oci-a1, backend at `8c09419`, the same one-off container and throwaway `postgres:18-alpine` as the 2026-10-19 run (credentials by `--env-file`, never read or printed; `-e PAYPAL_ENV=sandbox`), both removed afterwards. `python -m tidehold.bootstrap.sandbox smoke`, exit 0:

| Flow | Outcomes | State | Captured | Refunded | Refunds read back from the order by `invoice_id` | PayPal IDs |
|---|---|---|---|---|---|---|
| Deposit: CAPTURE with the sandbox card, then two partial refunds | succeeded, succeeded | captured | USD 30.00 | USD 25.00 | 2 of 2 listed, `COMPLETED` | order `294854529V972263S`, capture `5PP1688468866625C` |
| Balance: CAPTURE with the sandbox card, then a refund | succeeded | captured | USD 30.00 | USD 10.00 | 1 of 1 listed, `COMPLETED` | order `37685915TK052910R`, capture `5U293273CE6568632` |
| Balance: AUTHORIZE, then final capture of two thirds (kept until step 8c) | succeeded | captured | USD 20.00 | — | — | authorization `1PL54427FP600173F`, capture `2TJ316514E424474T` |
| Balance: AUTHORIZE, then void (kept until step 8c) | succeeded | voided | — | — | — | authorization `8X625636T25266948` |

Read: PayPal's order read lists each refund with the `invoice_id` tidehold sent, so reconciliation can find a refund whose answer was lost (plan step 4, D4). Each refund completed at once with a card capture; a `PENDING` refund was not seen. The fallback demo's `/deposits` answered 200 afterwards.

### Sandbox smoke (2026-10-19 12:24 UTC, PayPal sandbox)

On oci-a1, backend at `1e09065`, one-off `ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim` container with the shared sandbox credentials passed by `--env-file` (D13; never read or printed) and `-e PAYPAL_ENV=sandbox` (the shared file does not set it, so the guard refused the first run), against a throwaway `postgres:18-alpine` on its own network, both removed afterwards. `python -m tidehold.bootstrap.sandbox smoke`, exit 0:

| Flow | Outcome | State | Captured | Refunded | PayPal IDs |
|---|---|---|---|---|---|
| Deposit: CAPTURE order with the sandbox card, then refund half | succeeded | captured | USD 30.00 | USD 15.00 | capture `0L4141268S540481P` |
| Balance: AUTHORIZE order with the sandbox card, then final capture of two thirds | succeeded | captured | USD 20.00 | — | authorization `34X22452UN580151L`, capture `91K98585SC242181U` |
| Balance: AUTHORIZE order with the sandbox card, then void | succeeded | voided | — | — | authorization `8G377790DU147642J` |

Read: a card-funded CAPTURE order completes its capture at creation, as `_open` assumes. The fallback demo's `/deposits` answered 200 afterwards.

### Reauthorization check (input to the deposit/balance ADR's revocation trigger)

State file `~/tidehold-reauth/state.json` on oci-a1, outside every rebuilt database.

- Day 0, 2026-10-19 12:25 UTC (`reauth create`, exit 0): card authorizations `7PV67075PS815094S` (to reauthorize, then capture the new ID) and `82A06628HV495805W` (to capture on day 4+ without reauthorizing), both `CREATED`, PayPal sets `expiration_time` to 2026-11-17, 29 days later. PayPal-account order `7R9448445U854170R` created, waiting for a sandbox buyer's approval.
- PayPal-account hold, not placed yet. Order `7R9448445U854170R` (no `payment_source`, approval link built as `checkoutnow?token=`): after the developer approved it with a sandbox buyer, `reauth authorize` got 422 `ORDER_NOT_APPROVED` and GET showed `CREATED` with no payer. Order `79K36328YS748142H` (backend `a3f5d2d`, `payment_source.paypal.experience_context` with a return URL, PayPal's `payer-action` link): approved again, same 422, GET shows `PAYER_ACTION_REQUIRED` with no payer. PayPal recorded neither approval; cause not found yet (both orders kept in the state file).
- Day 4 (`reauth day4`): not before 2026-10-23 12:25 UTC for the cards.
