# Step 4 (D18) design: capture and refunds only, additive

Plan step 4, "Scope (D18)" and "Acceptance (D18)" (`../2026-10-19-tidehold-hackathon.md`); ADR `docs/adr/2026-10-23-balance-paid-before-trip-weather-refunds.md` D2, D4 and the amendment (refund window); D20. Written 2026-10-23 at f230910, before building; reviewed at start and before commit (findings at the end).

The change only adds. The hold paths (AUTHORIZE, capture of an authorization, void, reauthorize) stay until step 8c, because 8a's settlement and the e2e still run them; `make check` stays green at every commit.

## Data (migration 0012)

- `payments.intent` (`capture` | `authorize`): every deposit `capture`; existing balances `authorize`; a check allows `authorize` only for a balance. `commands.create_payment` takes the intent; today's callers keep their behaviour (deposit `capture`, balance `authorize`), and step 6 creates balances with `capture`.
- `payments.order_id`: the PayPal order that placed the payment, set when the `open` command succeeds (checkout and test card alike). Reads go through it.
- `payments.captured_at`: set when a capture is applied (an `open` that captured, or 8a's capture of a hold), from the database clock at completion; backfilled from the completing command. A check requires it on a captured payment. The refund window counts from it. It is our completion time, not PayPal's `create_time`: when a capture is completed late by reconciliation, our window is longer than PayPal's by that delay, and PayPal's refusal then becomes 8a's refused-refund ask.
- `money_commands.order_id`: allowed on any `open` attempt (today checkout only), so a test-card attempt keeps the order it created.
- `money_commands.resend_refused_at`: stamped when PayPal refuses a resend.
- `money_commands_once` loses its refund part: one live `open` per payment and one live closing command (capture or void) per hold remain until 8c.
- New `money_commands_one_refund_per_proposal`: unique on (proposal_id, payment_id) where `kind = 'refund'` and `status <> 'rejected'`. A retried or concurrent `settle_proposal` cannot refund a payment twice for one proposal; different proposals can each refund it.
- `refunded_minor <= captured_minor` stays.

## Commands (`money/commands.py`)

- `NEEDS_STATE["refund"]` becomes "captured, either purpose".
- `record_command(..., kind="refund", now=...)` locks the payment row (`for update`) and refuses:
  - `REFUND_WINDOW_CLOSED` when `now > captured_at + 180 days`, so PayPal is never called;
  - `REFUND_EXCEEDS_CAPTURED` when the amount exceeds captured − refunded − the amounts of the payment's refunds still open (`pending` or `unknown`).
  Two concurrent refunds serialize on the row lock, so the second sees the first as open.
- `now` is passed in like every due action's clock (runtime ADR). 8a's `settlement.execute.settle` gains `now` from `settle_due` to pass it on (a signature change only).
- `complete(..., "unknown", result=...)` keeps PayPal's answer for a refund that PayPal answered `PENDING`; `_apply` runs only on `succeeded`, so `refunded_minor` moves when the refund completes.

## Execution and reconciliation (`money/service.py`, `money/paypal.py`)

- `paypal.py` adds `get_order` (with its captures, authorizations and refunds), `get_capture` and `get_refund`, and `Refund` gains `invoice_id`.
- `_open` chooses CAPTURE or AUTHORIZE from `payments.intent`, not the purpose; `start_checkout` too. The result of a test-card or checkout `open` carries `orderId`, stored on the command and on the payment.
- A refund answered `PENDING` completes as `unknown` with error code `PAYPAL_REFUND_PENDING` and schedules `reconcile_payment`; `COMPLETED` succeeds; any other status is unexpected (stays open).
- On a refusal of a resend, the command stays `unknown`, as today, and is stamped `resend_refused_at`.
- `reconcile` reads before it resends (ADR D4), for commands that are `unknown` and for stamped ones:
  - **open**: with an order id, `get_order`. A completed capture (or authorization, for an `authorize` payment) completes the command with it. An order that PayPal shows not yet completed is resent under the same request id. Without an order id (an unsent or unanswered create), the command is resent.
  - **refund**: with a stored refund id (a `PENDING` answer), `get_refund`; otherwise `get_order` on the payment's order, matched on our `invoice_id`. `COMPLETED` succeeds, `FAILED` or `CANCELLED` rejects, `PENDING` stays open. Not found: resent, unless stamped.
  - A stamped command whose read cannot decide is left open and not resent again (step 8b asks the operator).
  - The hold kinds (capture of an authorization, void, reauthorize) keep today's resend without a read. They have no order-level read in scope, and step 8c removes them.
- `reconcile_due` keeps raising `RetryLater` while an outcome is unknown, so a pending refund is read again with backoff; after the attempts run out the action fails and shows in the console's failed actions (step 9).

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

Filled in after the review.
