# Step 4 (D18) design: capture and refunds only, additive

Plan step 4, "Scope (D18)" and "Acceptance (D18)" (`../2026-10-19-tidehold-hackathon.md`); ADR `docs/adr/2026-10-23-balance-paid-before-trip-weather-refunds.md` D2, D4 and the amendment (refund window); D20. Written 2026-10-23 at f230910, before building; reviewed at start and before commit (findings at the end).

The change only adds. The hold paths (AUTHORIZE, capture of an authorization, void, reauthorize) stay until step 8c, because 8a's settlement and the e2e still run them; `make check` stays green at every commit.

## Data (migration 0012)

- `payments.intent` (`capture` | `authorize`): every deposit `capture`; existing balances `authorize`; a check allows `authorize` only for a balance. `commands.create_payment` takes the intent; today's callers keep their behaviour (deposit `capture`, balance `authorize`), and step 6 creates balances with `capture`.
- `payments.order_id`: the PayPal order that placed the payment, set when the `open` command succeeds (checkout and test card alike). Reads go through it.
- `payments.captured_at`: the `sent_at` of the command that captured (an `open` that captured, or 8a's capture of a hold); backfilled the same way. A check requires it on a captured payment. The refund window counts from it. The first send is never after PayPal's own capture time, so the window closes no later than PayPal's, without parsing PayPal's `create_time` (review finding 9).
- `money_commands.order_id`: allowed on any `open` attempt (today checkout only), so a test-card attempt keeps the order it created, also when PayPal's answer could not be recorded but named its order (review finding 3).
- Checks: `intent = 'capture' or purpose = 'balance'`, `intent = 'authorize' or state <> 'authorized'`, a captured payment has `captured_at`, a placed payment has `order_id` (backfilled from the command's `order_id` or `result.orderId`); `intent` has no default (review finding 12).
- `money_commands.resend_refused_at`: stamped when PayPal refuses a resend.
- `money_commands_once` loses its refund part: one live `open` per payment and one live closing command (capture or void) per hold remain until 8c.
- New `money_commands_one_refund_per_proposal`: unique on (proposal_id, payment_id) where `kind = 'refund'` and `status <> 'rejected'`. A retried or concurrent `settle_proposal` cannot refund a payment twice for one proposal; different proposals can each refund it.
- `refunded_minor <= captured_minor` stays.

## Commands (`money/commands.py`)

- `NEEDS_STATE["refund"]` becomes "captured, either purpose".
- `record_refund(conn, payment_id, *, amount_minor, now, proposal_id)` (its own function: ruff's argument limit, and `now` is then required, review finding 14) locks the payment row (`for update`) and refuses a non-positive amount, and:
  - `REFUND_WINDOW_CLOSED` when `now > captured_at + 180 days`, so PayPal is never called;
  - `REFUND_EXCEEDS_CAPTURED` when the amount exceeds captured − refunded − the amounts of the payment's refunds still open (`pending` or `unknown`).
  Two concurrent refunds serialize on the row lock, so the second sees the first as open.
- `now` is passed in like every due action's clock (runtime ADR). 8a's `settlement.execute.settle` gains `now` from `settle_due` to pass it on (a signature change only).
- `complete(..., "unknown", result=...)` keeps PayPal's answer for a refund that PayPal answered `PENDING`; a later call without a result keeps the earlier one (`coalesce`, review finding 8); `_apply` runs only on `succeeded`, so `refunded_minor` moves when the refund completes.

## Execution and reconciliation (`money/service.py`, `money/paypal.py`)

- `paypal.py` adds `get_order` (with its captures, authorizations and refunds), `get_capture` and `get_refund`, and `Refund` gains `invoice_id`.
- `_open` chooses CAPTURE or AUTHORIZE from `payments.intent`, not the purpose; `start_checkout` too. The result of a test-card or checkout `open` carries `orderId`, stored on the command and on the payment.
- A refund answered `PENDING` completes as `unknown` with error code `PAYPAL_REFUND_PENDING` and schedules `reconcile_payment`; `COMPLETED` succeeds; any other status is unexpected (stays open).
- On a refusal of a resend, the command stays `unknown`, as today, and is stamped `resend_refused_at`.
- `reconcile` reads before it resends (ADR D4), for commands that are `unknown`, stamped, or `pending` with `sent_at` set (sent, answer lost; review finding 4):
  - **open**: with an order id, `get_order`. Only the collection the payment's intent asks for counts, and only a `COMPLETED` capture (or `CREATED` hold) of exactly the command's amount in USD completes it, in the shape `_open` records; anything else placed stays undecided. With nothing placed: `APPROVED` (checkout) is resent under the same request id; `CREATED`, `PAYER_ACTION_REQUIRED` or `VOIDED` is rejected (nothing placed, and nothing will be without the buyer; a resend would only be refused and keep the guest's attempt blocked); an order PayPal does not show (404) is resent under its key, as the plan says, since a 404 does not prove nothing was captured. Without an order id the command is resent (review finding 2; closing review finding 6).
  - Deviation from the plan's wording ("not captured: resend"): the rejection of `CREATED`, `PAYER_ACTION_REQUIRED` and `VOIDED` orders. Reported to the developer.
  - **refund**: with a stored refund id (a `PENDING` answer), `get_refund`; otherwise `get_order` on the payment's order, matched on our `invoice_id`. `COMPLETED` of the command's amount succeeds, `FAILED` or `CANCELLED` rejects, `PENDING` stays open. Not found in a successful read: resent, unless stamped.
  - A read that fails (timeout, 5xx, any 4xx other than an order's 404) proves nothing: no resend, no `complete` (review finding 7).
  - A command settled by a read that carries a proposal schedules `settle_proposal` again, so a settlement that stopped waiting (a refund long `PENDING`) still finishes (review finding 6).
  - A stamped command whose read cannot decide is left open and not resent again (step 8b asks the operator).
  - The hold kinds (capture of an authorization, void, reauthorize) keep today's resend without a read. They have no order-level read in scope, and step 8c removes them.
- `reconcile_due` keeps raising `RetryLater` while an outcome is unknown, so a pending refund is read again with backoff; after the attempts run out the action fails and shows in the console's failed actions (step 9). A stamped command whose read cannot decide also returns `unknown`, so its action fails the same way; that is intended until 8b's ask.
- 8a's `settle` gains `now` (from `settle_due`); `_send` treats a command already sent as unknown and schedules `reconcile_payment` for it (a run that stopped after the send leaves nothing else to resolve it), and a run that finds its settlement's refund already recorded by a concurrent run (checked under the payment's lock before the amount bound, backed by the unique index) returns without sending (review finding 1; closing review findings 1 and 2).

## Sandbox smoke (`bootstrap/sandbox.py`)

- The deposit flow refunds twice (USD 15.00, then USD 10.00).
- A new balance flow places a `capture`-intent balance by test card and refunds part of it.
- After the flows, the smoke reads each order and reports, per refund command, whether a refund with its invoice id is listed and its status.
- The hold flows and `reauth` stay until step 8c. Running it is the developer's (credentials on the VM).

## Fake PayPal (`tests/integration/fake_paypal.py`)

- Orders keep their captures, authorizations and refunds as they happen, so `GET /v2/checkout/orders/{id}` answers what PayPal would show; `GET` of a capture and a refund too.
- Knobs: `refund_status` (`COMPLETED` or `PENDING`, changeable later), `lose_first` (a call that fails with no effect, so a read finds nothing). The existing `time_out_first` keeps "took effect, answer lost".

## Tests (plan's acceptance names)

- `test_a_payment_is_refunded_twice_and_a_third_refund_above_its_capture_is_refused`
- `test_two_concurrent_refunds_never_exceed_the_capture` (two connections; mutation: open refunds left out of the bound)
- `test_an_unknown_open_is_resolved_from_its_order_before_any_resend` (asserts GET and no POST; mutation: resend first)
- `test_an_unknown_refund_is_found_in_the_order_by_its_invoice_id`
- `test_a_pending_refund_stays_open_until_paypal_completes_it`
- `test_a_test_card_payment_keeps_its_order_id`
- `test_a_refused_resend_is_read_before_any_ask`
- `test_a_proposal_refunds_a_payment_once_when_settled_twice` (in the settlement suite, two concurrent `settle` runs of a 50% whole-trip refund; mutation: drop the per-proposal index)
- `test_a_refund_past_the_window_is_refused_before_paypal_is_called` (mutation: 181 days)
- Changed: `test_a_second_live_command_of_the_same_kind_is_refused` (adds a refund above the capture), `test_a_deposit_is_captured_then_refunded`, `test_a_refused_resend_keeps_the_command_open_and_its_key` (a refund lost on its first send, refused on resend, read first and left open with its key and stamp), `test_the_smoke_runs_the_three_money_flows`.
- Unit: the reads parse captures and refunds.

## Design review at start (2026-10-23)

An independent reviewer read the design against the plan, the ADR and the code, read-only, before code was written: 16 findings (2 HIGH, 9 MED, 5 LOW). None needed a decision from the developer: findings 3 and 16 concern behaviour the plan already decides or step 4 does not change.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | The concurrent-settle test can pass by interleaving luck; the losing run raises a unique violation or resends a command the winner claimed | HIGH | Changed: the test holds the deposit's row until both runs wait on it; the loser returns without sending; `_send` leaves a sent command to `reconcile_payment`. A flake seen once in a full run (the loser sending the winner's unsent command) led to that last rule; 10 reruns since, 0 failures |
| 2 | Completing a command from a read had no amount, currency, status or intent check | HIGH | Changed: the read uses `_open`'s own check (`_placed_by`) per intent, explicit status maps for orders and refunds, amount checked for refunds |
| 3 | A test card's order id was stored only on success | MED | Changed: an unexpected answer that names its order keeps the id (`OutcomePending`), tested. The question "stop resending an order-less create after some age" is rejected for step 4: that resend is the ported behaviour, unchanged by D18; PayPal's request-id retention is already a threat-model line |
| 4 | A command sent but still `pending` skipped the read | MED | Changed: `sent_at` set counts as unknown for the read |
| 5 | The window and bound refusals abort 8a's `settle` for the remaining guests | MED | Rejected for step 4: no 8a path reaches them (8a refunds only whole-trip cancellations, netted below the captured deposit, minutes after the capture); 8b turns them into the operator's ask with the reason, as the plan says |
| 6 | A `PENDING` refund leaves the settlement `accepted` after its action fails | MED | Changed: a command settled by a read reschedules its proposal's `settle_proposal` |
| 7 | Reads did not separate "cannot read" from "not found" | MED | Changed: a failed read changes nothing; an order 404 rejects an `open` |
| 8 | A later `complete` wiped the `PENDING` refund id | MED | Changed: `result = coalesce(new, old)` |
| 9 | `captured_at` from our completion time can outlast PayPal's window | MED | Changed: the capture's `sent_at`, never after PayPal's time. A margin below 180 days is not added: the plan fixes 180 days and its mutation (181) |
| 10 | That an order read lists refunds with our `invoice_id` is unverified | MED | Recorded: the plan decides the match; the sandbox smoke checks it on the VM (`refunds_in_order`) before step 8b relies on it. If it fails, unknown refunds are resent under their key, safe while PayPal keeps the key |
| 11 | No test catches dropping the row lock | MED | Changed: the concurrency test forces both refunds to wait on the row; mutation "drop the row lock" fails it |
| 12 | Migration details (constraint name, checks, no default, order id backfill) | LOW | Changed: `money_commands_check1` verified on a migrated database and dropped by name; checks and backfill added |
| 13 | Refunds without a proposal escape the per-proposal index; zero refunds | LOW | Changed: a non-positive refund is refused; refunds without a proposal (the sandbox smoke) are bounded only by the capture, stated in the migration |
| 14 | `now` must be required for refunds | LOW | Changed: `record_refund` requires it |
| 15 | Threat model, the cap test rename, coverage, quality-lock counts missing | LOW | Changed: threat-model surface 2 in the same commit; the rename belongs to step 8c in the plan (the test still sends a void), and the threat model now says so; coverage and counts with the acceptance |
| 16 | A stamped refund that a read shows absent stays open forever | LOW | Rejected for step 4: the plan leaves a read that cannot decide to step 8b's ask |

## Code review before commit (2026-10-23)

An independent reviewer read the committed change (`e83ff54`) against the design, the plan and the ADR, ran the suites and probed findings 1 and 2 with a temporary test (deleted). 9 findings (1 HIGH, 2 MED, 6 LOW); all fixed in the next commit.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | A settlement command sent but never answered (the run stopped after the send) stayed stuck: `_send` waited for a reconcile nothing had scheduled; regression against 8a, voids included | HIGH | Fixed: `_send` schedules `reconcile_payment`; `test_a_settlement_refund_sent_before_a_crash_is_read_and_finished`, mutation (no schedule) fails it |
| 2 | With a full refund the losing concurrent run hit `REFUND_EXCEEDS_CAPTURED` before the unique index | MED | Fixed: `record_refund` checks for the settlement's live refund under the lock first (`RefundAlreadyRecorded`), the index stays as the database's guard |
| 3 | The settled-twice test discarded the runs' outcomes | MED | Fixed: outcomes asserted (only `None` or `RetryLater`), 50% and 100%, and a direct insert proves the index; mutations (the loser raises; no index) fail it |
| 4 | `captured_at` backfill could take the hold's `open` instead of its capture | LOW | Fixed: the command kind follows the payment's intent |
| 5 | One command completed meanwhile aborted the rest of `reconcile` | LOW | Fixed: settling from a read is caught like a send |
| 6 | A 404 order read rejected the `open`, where the plan says resend | LOW | Fixed: resent under its key |
| 7 | A refund PayPal reports `FAILED` or `CANCELLED` lost that status | LOW | Fixed: the error code names it (`PAYPAL_REFUND_FAILED`) |
| 8 | `get_capture` untested; loose types; formatting | LOW | Fixed: unit test, `Refund` types, `isinstance`, the fake's knobs |
| 9 | The hold capture's refused resend lost its test, and `_read` skipped the "marked, not sent again" rule for hold kinds | LOW | Fixed: `test_a_refused_resend_of_a_hold_capture_is_marked_and_not_sent_again` found the skip; `_read` now applies the rule to every kind |

After these fixes, `make check`'s integration run failed once on `test_a_proposal_refunds_a_payment_once_when_settled_twice[100]`: both runs found the balance's unsent void and both claimed and sent it (same request id, so no money moved twice), and the later one got `MONEY_COMMAND_NOT_OPEN`. The race predates D18 (8a's `_send` executed any `pending` command); the new concurrent test exposed it. Fixed: `claim(..., first_send=True)` refuses a command already sent (`MONEY_COMMAND_ALREADY_SENT`), `_send` uses it and leaves such a command to `reconcile_payment`, which resends only after a read. 15 reruns of the concurrent and crash tests since, 0 failures.
