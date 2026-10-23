# Step 8b design: finalize, reauthorization and the balance capture

Plan step 8b (`../2026-10-19-tidehold-hackathon.md`), decisions D11 and D17 (ADR `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`), D16 (ADR `docs/adr/2026-10-19-settlement-triggers-and-model-role.md`, D3: unanswered asks and cancellations after finalize). Builds on `step8a-design.md`. Written 2026-10-23 at 41cec64, before building and before step 4's day-4 reauthorization check has a result. That result picks branch A (the plan's current scope) or branch B (the deposit/balance ADR's first revocation trigger has fired); both are written out below so the choice is a lookup, not a redesign. Reviewed by an independent reviewer before building (findings and dispositions at the end).

## Which branch the day-4 result selects

`python -m tidehold.bootstrap.sandbox reauth day4` (step 4, `docs/evidence.md`, "Reauthorization check") runs three PayPal calls on day 4 or later of two card-funded authorizations created 2026-10-19 12:25 UTC, all after the 3-day honor period:

- R: reauthorize card 1 (`7PV67075PS815094S`);
- C-R: capture the new authorization R returned;
- C-O: capture card 2's original authorization (`82A06628HV495805W`) without reauthorizing (the ADR's fallback).

| R | C-R | C-O | Branch | Why |
|---|---|---|---|---|
| accepted | accepted | any | A | D11 works as decided. A refused C-O only means the fallback would end in the refused-capture recovery; record it in `docs/evidence.md` |
| refused | — | accepted | A | The trigger needs both a refused reauthorization and refused late captures; the ADR's fallback is the path the sandbox takes for cards. Reauthorization stays in scope (the plan says to try it; the PayPal-account hold, for which PayPal documents reauthorization, is unmeasured) |
| refused | — | refused | B | The trigger has fired: neither a reauthorization nor a capture after the honor period works |
| accepted | refused | refused | B | Same consequence: no capture after the honor period succeeds |
| accepted | refused | accepted | none: stop and ask | The ADR has no answer for a reauthorization that succeeds but whose capture is refused while a capture of the original works. Candidate: skip reauthorization and capture the original; needs the developer's decision and an ADR note |
| unknown (5xx, timeout) | | | none | Not a result: rerun `day4` (the state file keeps the request ids, so a rerun repeats nothing) |

The PayPal-account hold was never placed (`docs/evidence.md`), so the result covers card funding only. Measured behaviour is sandbox behaviour; tidehold runs only on the sandbox (product contract), so the sandbox decides.

## Branch A: the plan's current scope

### Flow

1. Money of a trip moves once, when the trip closes. It closes in one of two ways:
   - **Finalize**: a `finalize_trip` due action at the trip's finalize time (below) creates the trip's finalize proposal and runs the close.
   - **Whole-trip cancellation**: 8a's `settle_proposal` for an accepted `rest` cancellation from day 1 runs the same close at once, authorized by that settlement proposal (8a already sends its refunds and voids there; 8b adds the capture and the money of earlier accepted days).
2. The close computes, per guest, from every accepted settlement of the trip (pure, `settlement/close.py`):
   - deposit refund = sum of the chosen deposit `refund` lines − what the deposit already refunded;
   - balance owed = authorized − sum of the chosen balance `refund` lines (releases); balance `credit` lines do not reduce the capture (8a: "the balance portion is still captured");
   - balance credits = the chosen balance `credit` lines, written to `credits` (one row per settlement proposal, as 8a's unique key needs) when the capture succeeds.
3. Per guest, `settlement.plan.settle_payment` turns balance owed into a void, a partial capture or a capture (always `final_capture`). A capture whose authorization is past its honor period is preceded by a reauthorization; a refused reauthorization falls back to capturing the original authorization (D17).
4. Commands record the proposal that authorized them: the finalize proposal, or the whole-trip settlement. One refund per deposit and one closing command per hold, as the step-4 unique indexes require; aggregating the accepted days into one refund is what keeps day cancellations inside them.
5. After the close, the trip is closed (`trips.closed_at`): a new cancellation asks (`AFTER_FINALIZE`), and a settlement still asked cannot be accepted any more.

### Finalize time (`holds/schedule.py`)

- `finalize_at(trip, payments)` = the earlier of:
  - the end of the last local trip day (8a's `day_window`);
  - the earliest stored `authorization_expires_at` among the trip's authorized balances, minus `FINALIZE_MARGIN`.
- This follows the plan's step 8 scope ("finalize before `authorization_expires_at`, not at the end of the last local trip day"). A hold opened at UTC midnight of T-(29 − N) expires at the UTC end of the last trip day, so for a trip west of UTC finalize lands before the local day ends: at UTC−10 with a 6 h margin it is 08:00 local on the last day, and a cancellation later that day asks (question Q3).
- `FINALIZE_MARGIN` covers the worst-case retry budget of the `finalize_trip` action (8 attempts, backoff 0.5 to 30 min, each attempt's lease) so the last retry still runs before expiry; a unit test derives the budget from `due_actions.MAX_ATTEMPTS`, `backoff` and `LEASES` instead of trusting a constant.
- A null `authorization_expires_at` (PayPal answered without `expiration_time`) is read as the authorization time + 29 × 24 h.
- `ensure_schedules` derives one `finalize_trip` per trip that has a requested balance, is not closed and not cancelled as a whole, due at `finalize_at`. Like `check_trip`, it is recomputed when no action is open, so a guest authorizing late (a later expiry) never moves it later than a stored action, and an earlier expiry is seen on the next tick only if the stored action has not started: the handler recomputes `finalize_at` and raises `RetryLater` when it is still in the future.

### Reauthorization (`settlement/close.py`, per guest)

- Honor period: 3 days from `payments.authorized_at` (new column, set by the `open` and `reauthorize` successes). PayPal documents a new 3-day honor period for a reauthorized authorization, without extending the 29 days (deposit/balance ADR constraints; not measured).
- Inside the honor period (`now < authorized_at + 3 days`) the capture goes straight to the current authorization, with no reauthorization: PayPal allows a reauthorization only after the honor period (deposit/balance ADR constraints).
- From `now ≥ authorized_at + 3 days` on, record a `reauthorize` command for the owed amount (≤ the authorized amount), authorized by the closing proposal, and execute it before the capture.
- The decision is taken again on every attempt with that attempt's `now`: a capture whose retry crosses the end of the honor period is reauthorized on the retry that finds it over.
  - succeeded: `authorization_id` is the new one; the capture is recorded against it (`record_command` reads the target when it records).
  - rejected: capture the original authorization (the ADR's fallback); `payment_events` keeps the refusal.
  - unknown: `RetryLater`; `reconcile_payment` resends it under the same request id.
- `authorization_expires_at` keeps the earlier of the stored and the returned value, since a reauthorization does not extend the validity.
- Every capture asserts `now < authorization_expires_at` and hold age (now − the original authorization time) < 29 days, and refuses with `HOLD_EXPIRED` otherwise: the refusal becomes a `money_refused` ask, never a send.

### Refused capture: a second balance payment (D17)

- `payments.guest_id` (new, workspace-consistent foreign key, backfilled) gives every payment its guest; `guests.balance_payment_id` keeps pointing at the guest's current balance payment.
- `payments.intent` (`capture` | `authorize`, new; deposits `capture`, balances `authorize`) lets a recovery balance be a CAPTURE order; `money.service._open` and `start_checkout` read the intent instead of the purpose.
- A capture refused by PayPal opens a `money_refused` ask (8a's kind, subject `command:<id>`) showing the guest, the owed amount and PayPal's issue code. Accepting it creates the second balance payment (intent `capture`, amount = the refused capture's amount, `guest_id` set) and points `guests.balance_payment_id` at it, so the guest's link offers a checkout for that amount; dismissing it records that the operator settled it outside tidehold (Q2).
- A balance credit of that guest is written when the recovery payment captures.
- The trip timeline and `GET /failed-actions` read payments through `payments.guest_id`, so the first payment's events (`capture_rejected`) and the second payment's stay in the timeline.
- The guest API returns each payment's intent; loading the JS SDK with it on the guest page belongs to step 9's 8b screens.
- A refused refund keeps 8a's ask ("settle it with the guest outside tidehold"): a new checkout cannot return money to a guest (Q1).

### A command left open after a refused resend

- `money.service.execute` already keeps such a command `unknown`, because its first send may have taken effect. 8b stamps `money_commands.resend_refused_at` there.
- A settlement sweep (in `execute.ensure_schedules`) opens one `command_open` ask per such command (subject `command:<id>`): "PayPal refused a resend of the <kind> for <guest>; the first send may have moved money; check PayPal and dismiss". Money cannot import settlement (layers), hence the sweep.
- While the ask is open, the close treats that guest's line as waiting; once the operator dismisses it, the line counts as settled outside tidehold and the closing proposal can become `executed` without sending anything new for it. The command itself stays open, so no second live command of that kind can be recorded.

### Unanswered asks and cancellations after the close (D16 ADR, D3)

- A settlement proposal still `asked` at the close contributes nothing: the hold is captured per the trip. It stays visible; accepting it afterwards is refused (`TRIP_CLOSED`) and its question gains the reason `AFTER_FINALIZE`.
- A cancellation recorded after the close is stored and evaluated as in 8a (the guest page can show it), but its proposal is always `asked` with `AFTER_FINALIZE`, and its only decision is dismiss: refund after capture is out of scope (D16 ADR, D3).
- The close and `record_cancellation` both lock the trip row, so a cancellation is either covered by the close or recorded after it.

### Data (migration 0012)

- `payments`: `guest_id`, `intent`, `authorized_at`.
- `money_commands.resend_refused_at`.
- `trips.closed_at`.
- `proposals.kind` adds `finalize` (subject `trip`) and `command_open`; reason `AFTER_FINALIZE` joins `evaluate.Reason`.
- `due_actions.kind` adds `finalize_trip` (subject: trip); its lease is 900 s (up to three PayPal calls of 20 s per guest, 12 guests).

### Acceptance → tests (branch A)

| Plan acceptance item | Test |
|---|---|
| Outcome type: balance partial capture | `test_finalize_partially_captures_what_accepted_days_leave_owed` |
| Outcome type: balance capture | `test_finalize_captures_the_whole_balance_when_nothing_was_cancelled` |
| Capture refused → ask; the new checkout's payment and the first one's events stay in the timeline | `test_a_refused_capture_asks_and_its_new_checkout_keeps_both_payments_in_the_timeline` |
| A refused reauthorization captures against the original authorization | `test_a_refused_reauthorization_captures_the_original_authorization` |
| The last settlement action falls inside the validity for the longest allowed trip | unit `test_the_last_settlement_action_falls_inside_the_validity` (N = 1..10, time zones UTC−12 to UTC+14, finalize time plus the retry budget before expiry) |
| An ask unanswered at finalize captures per the trip | `test_an_ask_unanswered_at_finalize_captures_per_the_trip` |
| A cancellation after finalize asks | `test_a_cancellation_after_finalize_asks` |
| Hold age < 29 days asserted | asserted in every capture test; `test_a_hold_29_days_old_is_never_captured` |
| Mutation: hold a 3-day trip so its last day is outside the validity → a test fails | open the hold a day earlier (T-(30 − N)) and the validity test fails |
| Mutation: skip the reauthorization outside the honor period → a test fails | `test_a_capture_after_the_honor_period_is_reauthorized_first` |
| (developer review) No reauthorization inside the honor period; a retry that crosses it decides again | `test_a_capture_inside_the_honor_period_sends_no_reauthorization`; `test_a_capture_retried_past_the_honor_period_is_reauthorized_on_that_retry` |
| Both 8a and 8b: no settlement test touches a provider; evidence time < decision time | the settlement suite's raising provider stub stays installed; 8a's evidence-time assertion is unchanged |

Scope items with no acceptance line in the plan get a test too: `test_a_whole_trip_cancellation_captures_what_is_owed_at_once`, `test_finalize_refunds_the_deposit_of_accepted_days_once`, `test_a_balance_credit_is_written_when_its_capture_succeeds`, `test_a_command_refused_on_resend_becomes_an_ask`, `test_a_failed_action_of_the_first_balance_payment_still_shows` (step 9's note).

## Branch B: the sandbox refuses reauthorization and late captures

The deposit/balance ADR's first revocation trigger names two directions: "move evaluations inside the honor period or ask the operator earlier". Both change a decided ADR (D11), so the developer picks one in stage 2; nothing of B is built before that. What stays from A in either: the close (whole-trip and finalize), the aggregated deposit refund, the second balance payment after a refused capture, the open-command ask, the after-finalize ask, `payments.guest_id`.

### B1: finalize inside the honor period

- Hold timing: the balance hold opens 3 days minus the retry budget before the trip's finalize time (D11's T-(29 − N) becomes "finalize − 3 days + margin"), so every capture is inside the honor period and no reauthorization exists. An authorization made before that window is impossible because the payment does not exist yet.
- Trip length: a hold that must be placed before the first trip day allows trips of at most 2 days (the window opens on or before the first day only when N ≤ 2 with the margin). Longer trips need guests to approve during the trip, which a liveaboard without connectivity cannot do. Sub-decision: `MAX_TRIP_DAYS` 2, or allow on-site approval up to 10 days.
- 8a's "a guest without an authorization by the first trip day becomes an ask" becomes "by the hold window's opening plus one day".
- Scope (8b, replacing the current text): "finalize on the last trip day inside the honor period of every hold (one capture with final_capture, or a void); a refused capture or refund becomes an ask with a new checkout as a second payment of the guest; a command open after a refused resend becomes an ask; an ask unanswered at finalize captures per the trip; a cancellation after finalize asks."
- Acceptance (8b): "tests per outcome type (balance partial capture, capture); capture refused → ask, both payments in the timeline; every capture falls inside the honor period for the longest allowed trip in every time zone; an ask unanswered at finalize captures per the trip; a cancellation after finalize asks; asserting hold age < 3 days at capture; mutations: open the hold one day earlier → the honor test fails; let a capture run past the honor period → a test fails."
- Changes with it: step 6 code and tests (`holds.schedule.balance_opens_at`, `MAX_TRIP_DAYS`, `test_the_balance_hold_opens_at_t_minus_29_minus_n`, the "day 28" parametrized test, its mutation).

### B2: ask the operator earlier, capture inside the honor period, refund later

- Hold timing stays T-(29 − N). On hold day 2 (inside the honor period) the operator is asked to confirm each guest's capture of the full balance; accepted captures move money then, weeks before the trip. Weather cancellations afterwards become refunds of the captured balance.
- Cost: guests pay before the weather is known, which the ADR rejected as option D ("refunds lose fees"); refund after capture comes into scope, so D16 ADR D3 and the one-refund-per-payment index change (several partial refunds bounded by the captured amount).
- Scope (8b, replacing): "on hold day 2 an operator ask confirms each guest's balance capture (final_capture); a confirmed capture runs inside the honor period; a later accepted cancellation refunds the captured balance per the policy; refused captures and refunds become asks (new checkout for a capture); a command open after a refused resend becomes an ask; a hold the operator did not confirm by the end of the honor period is voided."
- Acceptance (8b): "tests per outcome type (capture, refund after capture, void of an unconfirmed hold); no capture after the honor period; refunds never exceed the captured amount across several cancellations; mutations: capture on day 4 → a test fails; let two refunds exceed the captured amount → a test fails."

### Plan rows and ADR sections that change under B (either option)

- Plan: Premises (the PayPal authorization line gets the measured result); Invariants row "Every capture happens within the authorization's validity; outside the honor period a reauthorization precedes capture"; Scope "Don't" (trips that cannot settle inside one authorization's validity); D11 (a new decision entry with date and decider); step 6 Scope and Acceptance (B1); step 8 Scope (reauthorization and its fallback); 8b Scope and Acceptance (texts above); step 9's "waits for 8b" (no reauthorized state); the 8b Progress row.
- `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`: D11 and the Failures line superseded (a new ADR, the old one `status: superseded` with a review note naming the day-4 evidence), Expected Outcome ("a test fails if the last action of a 10-day trip falls after day 28"), Followup; B2 also D16 ADR D3 ("refund after capture is out of scope").
- `AGENTS.md` product contract line on the balance hold (T-(29 − N), reauthorization, 10 days); `requirements.md` R5 and the PayPal interface row; `docs/threat-model.md` row "A capture lands outside the authorization's validity"; the runtime ADR's context line naming T-(29 − N) gets a review note.

## Questions for the developer (stage 2)

- **Q1** The plan says "a refused capture or refund becomes an ask with a new checkout". This design gives a new checkout only to a refused capture; a refused refund keeps 8a's settle-outside ask, since a checkout cannot pay a guest. Confirm.
- **Q2** The second balance payment is created when the operator accepts the refused-capture ask (an operator act), not automatically at the refusal. Confirm, or create it at once and let dismiss withdraw it.
- **Q3** West of UTC the finalize time lands inside the last local day (UTC−10: 08:00 with a 6 h margin), so a cancellation later that day asks. The plan decided "before `authorization_expires_at`"; the alternative is opening holds at T-(28 − N), which changes step 6 and the contract line. Keep the plan's rule (recommended) or change the opening.
- **Q4** Only under the "stop and ask" row of the branch table: what to do when a reauthorization succeeds but its capture is refused while the original's capture works.
- **Q5** Under B: B1 (finalize inside the honor period; trips ≤ 2 days or on-site approval) or B2 (operator-confirmed capture inside the honor period, refunds after). Lean: B1 with on-site approval, because it keeps the balance held rather than charged, which is the ADR's reason for holding at all; its cost is guests approving during multi-day trips.

## Out of 8b

- Console screens for finalize, captures, reauthorization and the recovery checkout, and the SDK intent on the guest page (step 9).
- A live sandbox run of finalize (needs step 10's deploy; the live demo cannot show a reauthorization because holds are voided nightly, deposit/balance ADR followup).
- Refund after capture (D16 ADR, D3), except under B2.

## Design review at start (2026-10-23)

Filled in after the independent review.
