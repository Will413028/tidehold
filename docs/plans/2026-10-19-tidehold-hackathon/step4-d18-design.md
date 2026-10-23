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

## Local acceptance review (2026-10-23): three fixes

The developer reran step 4's acceptance locally (green) and an independent reviewer found three issues, none HIGH. Design of the fixes, before building:

1. **A refund refused when it is recorded stops only its own move.** `settlement.execute._record` also catches `Conflict` with `REFUND_WINDOW_CLOSED` or `REFUND_EXCEEDS_CAPTURED` and reports the move as refused before PayPal, with that code. `settle` opens 8a's `money_refused` ask for it, with subject `proposal:<id>:payment:<id>` (no command exists yet to name) and the code in the ask's facts and question, and goes on with the other guests' moves. A proposal with such a refusal is not marked `executed`: once nothing is unknown or pending, the action completes and the proposal stays `accepted` for step 8b. A PayPal refusal of a sent command keeps 8a's behaviour (ask, and `executed`), now with PayPal's code in the facts too.
2. **A refund PayPal reports `PENDING` is read until it ends.** `reconcile` reports such a command as `pending`, distinct from `unknown` (a read or send whose outcome we do not know). `reconcile_due` raises `RetryLater` while anything is `unknown`, as today, so a read that keeps failing still ends in a failed action after `MAX_ATTEMPTS`. When only `pending` remains it raises a new `due.Waiting(after)`: `run_due` puts the action back to `pending`, due after `PENDING_REFUND_READ_EVERY` (1 hour; a refund can stay pending for days, for example on an eCheck payment, as the developer's review notes; PayPal's documentation was not re-read in this round), without consuming an attempt. Reads go on until PayPal answers `COMPLETED`, `FAILED` or `CANCELLED`, as the plan's step 4 says; a webhook stays deferred (plan, Scope). When the read settles the refund, 8a's settlement is scheduled again (already the case) and becomes `executed`.
3. **The window is checked again before a refund's first send.** `commands.refund_window_closed(conn, command_id, now)` closes a refund that was never sent as `rejected` with `REFUND_WINDOW_CLOSED` when `now` is past its payment's window, without calling PayPal; `settlement.execute._send` calls it before `service.execute(first_send=True)` and treats the result like fix 1 (the ask with the code; the proposal stays `accepted`). Closing it frees the per-proposal index and the in-flight bound. A resend from reconciliation is not checked: that refund may already have reached PayPal, and only a read can tell.

Tests: `test_a_refund_refused_when_recorded_asks_and_the_other_guests_are_refunded` (two guests, one deposit captured over 180 days ago; mutation: catch only the unique-index signals), `test_a_pending_refund_is_read_until_paypal_completes_it` (PENDING for more than 8 reads, then COMPLETED; mutation: PENDING raises `RetryLater`), `test_a_refund_whose_window_closes_before_its_first_send_is_not_sent` (mutation: drop the check before the first send).

### Design review of the three fixes (2026-10-23)

An independent reviewer read the design above against the code, read-only, before building: 10 findings (2 HIGH, 6 MED, 2 LOW). The design was changed as follows before any code; what was built is this section, where it differs from the one above.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | A refusal at record time stored nothing, so a rerun of the settlement (a read that settles another command, the late sweep, a lease steal) recorded and could send the refund after the operator was told to settle it outside tidehold | HIGH | Changed: the refusal is stored as a `rejected` refund with `sent_at` null and its code (`record_refused_refund`, plus a `refund_refused` payment event); every rerun finds it final. The ask's subject is `command:<id>` for every refusal. `REFUND_EXCEEDS_CAPTURED` is final like the window: the developer's instruction sends both to the ask |
| 2 | Fix 3's closed refund looked like a PayPal refusal on a rerun, so the proposal became `executed` and a second ask opened | HIGH | Changed: one rule for both fixes, on every run: `rejected` with `sent_at` null is `refused` (never sent); only that keeps the proposal `accepted`. A refusal by PayPal of a sent command keeps 8a's behaviour |
| 3 | `settle_proposal` itself still failed after ~61 min while a refund was pending | MED | Changed: a refund whose stored result is `PENDING` is `processing`; `settle` completes without marking `executed`, and the read that ends the refund schedules it again. The pending test asserts no failed action |
| 4 | The window check before the first send sat in `settlement`, missing `reconcile`'s send of a never-sent refund and the sandbox CLI | MED | Changed: `money.service.execute` closes a never-sent refund past its window (`close_refund_past_window`, an update guarded by `status = 'pending' and sent_at is null`, so it cannot race a claim) before claiming; both paths are tested. It reads the database clock: PayPal's window runs on real time, so the moment of the send is what counts (recording checks the caller's clock) |
| 5 | "Without consuming an attempt" was unspecified; `attempts` is the lease's fencing token; failures were counted in total, not in a row | MED | Changed: `due_actions.failures` (new column, migration 0013) counts failures in a row and decides `MAX_ATTEMPTS` and the backoff; a stalled worker's lease taken over counts as a failure; `Waiting` resets it to 0 and leaves `attempts`, which only grows, as the fencing token; `Waiting` is not a `RetryLater` and is caught first. A read that succeeds and shows `PENDING` resets the count, reading the developer's "keep failing" as failures in a row |
| 6 | The sourced comment was missing; the plan's webhook trigger ("stays `PENDING` beyond the reconcile retries") no longer fits; nobody sees an endless pending refund | MED | Recorded: PayPal's refund reference could not be fetched from the build container (`getaddrinfo ENOTFOUND developer.paypal.com`), so the comment cites the developer's review and says it is unverified. The trigger's wording and whether a long pending refund should ask the operator are plan questions, put to the developer, not changed here |
| 7 | Several named tests would pass under their mutations; missing tests for failing reads and for pending beside unknown; the old pending test asserts `unknown` | MED | Changed: the refused guest is visited first, the test asserts the code, the actions `done`, the proposal `accepted`, and a rerun; new `test_a_refund_read_that_keeps_failing_ends_failed` and `test_a_pending_refund_beside_an_unknown_one_still_backs_off` (the fake can fail reads); the old test now expects `pending`; the window test runs through settlement and reconciliation and checks the refund no longer holds the bound or the index |
| 8 | Each hourly pending read wrote an event | LOW | Changed: an unchanged read writes nothing |
| 9 | Other refusals (`PAYMENT_NOT_AUTHORIZED`, `PAYMENT_NOT_CAPTURED`) still abort the settlement; `_refused` looked up a refund by the deposit | LOW | Recorded: only the two refusals the developer named are caught; the others mean a payment changed under a decided settlement, which 8b's late-payment proposal handles. Changed: `_refused` uses the move's own payment |
| 10 | What makes a proposal with a refused move `executed`; the refused amount still counts as returned in netting and caps | LOW | Recorded as questions for step 8b, put to the developer: 8b's scope does not say, and counting it as returned is 8a's existing behaviour for a PayPal refusal |

### Code review of the three fixes before reporting (2026-10-23)

An independent reviewer read commit `f7b9fda`, ran mutations of its own in a copy, and found 9 issues (no HIGH); six of its mutations survived the suites. Dispositions:

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | The failures-in-a-row mechanism had no test: no reset on wait, no failure on a lease takeover, no fencing in `wait`, `retry` counting claims again all survived | MED | Fixed: `test_waiting_is_not_a_failure_and_starts_the_failures_in_a_row_over`, `test_a_lease_taken_over_counts_as_a_failure`, `test_a_worker_that_lost_its_lease_cannot_make_the_action_wait`; each of those four mutations now fails one |
| 2 | Nothing pinned what counts as pending ("any read result" survived) | MED | Fixed: `test_a_refund_read_back_with_another_amount_is_not_waited_on` |
| 3 | The plan's deferred-webhook trigger ("stays `PENDING` beyond the reconcile retries") can no longer fire, and nobody is asked about a refund pending for days | MED | Put to the developer: rewording a deferred item is a plan decision; not changed |
| 4 | `failures` was added to 0012, which already ran on the local acceptance database; `migrate.py` would skip it there | LOW | Fixed: its own migration 0013, without the backfill |
| 5 | The concurrent branch of `_send` returned the raw status, so a pending refund another run stored read as `unknown` | LOW | Fixed: it maps through `_status` (no dedicated test; a concurrent settle beside a PENDING refund) |
| 6 | Two runs could store two refusals for one guest | LOW | Fixed: `record_refused_refund` takes the payment's lock and returns a refund of the settlement already stored (no dedicated test; it needs two runs interleaved on a refusal) |
| 7 | A payment's reconcile waiting its hour delays the read of a new lost send on the same payment | LOW | Recorded: it recovers when the hour is up, nothing is lost or sent twice; pulling an open action earlier changes `schedule` for every kind, which this round does not take on |
| 8 | `REFUND_EXCEEDS_CAPTURED` is final even when an open refund later fails | LOW | Recorded: the developer's instruction makes both refusals final and asked; no path reaches it in today's scope (only whole-trip settlements refund) |
| 9 | The console's failed-actions `failures` field (a row count) can be confused with the new column | LOW | Recorded: naming outside this round's three fixes |

## Second independent review (2026-10-23): three fixes, step 4's last round

A second reviewer, who did not read this file, reviewed `origin/main...ef8a237`: one MED (a regression of the round above) and two LOW. This is step 4's last round of fixes; after it only a correctness HIGH or MED blocks the merge, and anything else goes to the plan's Deferred with its trigger. Design, before building:

1. **A settlement waiting on a pending refund runs itself again (MED).** `settle` returned (its action `done`) while a refund was `processing` and relied on the read that ends the refund to schedule it again. When that read ends the refund while the settlement's action is still `running`, `schedule`'s `on conflict ... do nothing` drops the wake; the settlement then completes, and a refund PayPal ends `FAILED` never gets its `money_refused` ask (the proposal stays `accepted` for good). This is the threat model's "two workers, or a worker that resumes after its lease expired". Fix: while any move is `processing`, `settle` raises `due.Waiting(service.PENDING_REFUND_READ_EVERY, "PAYPAL_REFUND_PENDING")`, so its own action runs again on the same interval as the read, until every move is final; it no longer depends on an edge (the read's wake), which stays. Order of the checks after the moves: `unknown` or `pending` → `RetryLater` (as today); then `processing` → `Waiting`; then `refused` → complete without `executed` (as today); else `executed`. While the settlement waits (`pending`, due within the hour), the read's wake meets the open action and adds nothing, so the settlement sees the end of the refund up to `PENDING_REFUND_READ_EVERY` later; the wake still revives a settlement whose action ended (failed, or completed before this fix). A run of the waiting settlement calls nothing at PayPal: `_send` finds the stored command and only maps its status.
2. **A resend PayPal refused does not count as a failure of the payment's reconcile (LOW).** A command whose read says "resend" after PayPal refused a resend (`resend_refused_at` set) cannot be settled by a read or a send; it waits for step 8b's ask. `reconcile` reports it as `held` (new `Read` and `ReadOutcome` value), no longer `unknown`. `reconcile_due`: anything `unknown` (a read that failed, a send whose answer was lost, an answer it cannot record) → `RetryLater`, counted as before; else anything `pending` → `Waiting`; else anything `held` → `RetryLater("PAYPAL_RESEND_REFUSED")`, so a payment with only held commands still fails after `MAX_ATTEMPTS` for the operator console as today, until 8b asks. A read of a held command that itself fails (network, 5xx) stays `unknown` and counts. `test_a_pending_refund_beside_an_unknown_one_still_backs_off` stays as it is: its unknown command is a read that fails, which still backs off under the new rule; a new test covers a held command beside a pending refund.
3. **Reconciliation sends a never-sent command only as its first send (LOW).** `reconcile` decides "resend" for a command with `sent_at` null without reading PayPal (nothing was sent); meanwhile a settlement may claim it with `first_send=True` and send it, and reconcile's plain `claim` then succeeded and sent it again without a read (same request id, so no money moves twice, but a 409 would mark `resend_refused_at` wrongly). Fix: `reconcile` calls `execute(..., first_send=command["sent_at"] is None)`, using the row it read; a command sent since raises `MONEY_COMMAND_ALREADY_SENT`, which `reconcile` reports as `unknown`, so the action backs off and its next run reads PayPal first. Not ending the action matters: the concurrent sender schedules `reconcile_payment` when its answer is lost, and that wake meets this running action and is dropped.

Tests: `test_a_settlement_waiting_on_a_pending_refund_asks_when_paypal_fails_it` (the settlement's handler runs while a read ends the refund `FAILED`, so the read's wake is dropped; an hour later the settlement still runs and opens the `money_refused` ask with `PAYPAL_REFUND_FAILED`; mutation: `Waiting` back to `return`), `test_a_pending_refund_beside_a_refused_resend_is_read_until_it_ends` (more than `MAX_ATTEMPTS` reads with no failed action, then `COMPLETED`; mutation: `held` counted with `unknown`), `test_reconciliation_leaves_a_refund_a_settlement_just_sent_to_a_read` (two connections: the settlement's first send lands between reconcile's read of the row and its send; PayPal gets one refund request and `resend_refused_at` stays null; mutation: reconcile claims without `first_send`). No schema change.

### Design review of the second review's fixes (2026-10-23)

An independent reviewer read the design above against the code, read-only, before building: 7 findings (1 HIGH, 3 MED, 3 LOW). Dispositions; what was built is the design above with these changes:

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | The same dropped wake exists for `reconcile_payment`: a settlement's send whose answer is lost while the payment's reconcile runs (its command list taken before) schedules a read that is dropped; nothing reads the command again and the settlement fails | HIGH | Changed, level-triggered in the settlement like fix 1: on every run, for each move that is `unknown` or `processing`, `settle` checks the payment's latest `reconcile_payment` (`_being_read`) and schedules one when the last ended `done` or none exists. Test `test_a_settlement_reads_a_refund_whose_read_was_dropped`. A command outside a settlement (a checkout's open, the sandbox CLI) has no such second reader; step 10's nightly read covers it, as before |
| 2 | With fix 1 a settlement waits forever, never failing, once the refund's reads gave up (`reconcile_payment` failed) | MED | Changed: when the payment's latest `reconcile_payment` is `failed`, `settle` raises `RetryLater("PAYPAL_REFUND_NOT_READ")` instead of `Waiting`, so it fails too and shows in the console; it does not schedule another read over a failed one. Test `test_a_settlement_whose_pending_refund_is_no_longer_read_fails` |
| 3 | Fix 3's test had no seam between reconcile's read of the row and its send; the settlement's command must stay open; count POSTs; the `resend_refused_at` assertion could not fail | MED | Changed: the test wraps the public `service.execute` that `reconcile` calls, so the settlement's first send (answer lost, so the command stays open) lands just before reconciliation's; then PayPal refuses any further refund POST, so a blind resend would both count a second POST and mark `resend_refused_at` |
| 4 | Fix 1's test did not prove the wake was dropped | MED | Changed: it asserts one `settle_proposal` action, `pending`, after the interleaved run (under the mutation it is `done`) |
| 5 | A first send by reconciliation of a command another run already completed costs a spurious failure | LOW | Changed: on `MONEY_COMMAND_ALREADY_SENT` reconciliation reports the command's own status when it is no longer open, `unknown` only while it is (no dedicated test: it needs the other send to complete inside the window between reconcile's read and its claim) |
| 6 | The same send-without-a-read race between `complete_checkout` and reconciliation | LOW | Recorded in the plan's Deferred with its trigger (step 4's last round fixes only correctness HIGH/MED beyond the three named) |
| 7 | While a settlement waits, the late-payment sweep skips it; a completed refund leaves the proposal `accepted` up to an hour; the threat model's row should name the new tests | LOW | Recorded here (both delays are bounded by `PENDING_REFUND_READ_EVERY`), and the threat model's "runs twice" row now names the fix's tests |
