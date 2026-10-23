# Step 8b design: finalize, reauthorization and the balance capture

Plan step 8b (`../2026-10-19-tidehold-hackathon.md`), decisions D11 and D17 (ADR `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`) and D16 (ADR `docs/adr/2026-10-19-settlement-triggers-and-model-role.md`, D3: unanswered asks and cancellations after finalize). Builds on `step8a-design.md`.

Written 2026-10-23 at 41cec64, before building and before step 4's day-4 reauthorization check has a result. That result picks one of two branches:

- **A:** the plan's current scope.
- **B:** the deposit/balance ADR's first revocation trigger has fired.

Both branches are written out below, so choosing one is a lookup, not a redesign. The design was revised after the developer's review and an independent review (findings and dispositions at the end). Questions the plan does not answer are listed for the developer and are not decided here.

## Which branch the day-4 result selects

`python -m tidehold.bootstrap.sandbox reauth day4` (step 4, `docs/evidence.md`, "Reauthorization check") makes three PayPal calls on two card-funded authorizations created 2026-10-19 12:25 UTC. It runs on day 4 or later, after the 3-day honor period:

- **R:** reauthorize card 1 (`7PV67075PS815094S`).
- **C-R:** capture the new authorization that R returned.
- **C-O:** capture card 2's original authorization (`82A06628HV495805W`) without reauthorizing. This is the ADR's fallback.

A refusal counts only when it is PayPal's answer about the payment: a 4xx with an issue code. A 401 or 403, or a permission issue, says nothing about reauthorization; fix it and run again.

| R | C-R | C-O | Branch | Why |
|---|---|---|---|---|
| accepted | accepted | any | A | D11 works as decided. A refused C-O only means that the fallback ends in the refused-capture recovery; record it in `docs/evidence.md` |
| refused | not run | accepted | A | The trigger needs both a refused reauthorization and refused late captures. For cards, the sandbox takes the ADR's fallback. Reauthorization stays in scope: the plan says to try it, and the PayPal-account hold, for which PayPal documents reauthorization, is unmeasured |
| refused | not run | refused | B | The trigger as worded has fired |
| accepted | refused | refused | Q4 | No capture after the honor period succeeds, but no reauthorization was refused, so the wording of the trigger does not cover it. Treating it as B is this design's reading and is not decided |
| accepted | refused | accepted | Q4 | The ADR has no answer. Candidate: skip reauthorization and capture the original |
| any unknown (5xx, timeout) | | | none | Not a result. The current script cannot get one by rerunning: `_step` skips any step it has already recorded, `unknown` included (`sandbox.py:166`), and C-R never runs when R is unknown. A step 4 fix (resend an `unknown` step with its stored request id) is needed before a rerun. It is reported to the developer and not made in 8b |

Limits of the measurement:

- Day 4 is not day 28. D11 captures at about day 28 of the hold, while the check captures on day 4 or later. A second capture near day 27 (before the 2026-11-17 expiry) would confirm the late case (Q8).
- The PayPal-account hold was never placed (`docs/evidence.md`), so the result covers card funding only. Step 4's acceptance asks for that hold, so step 4's row stays open on that item unless the developer waives it.
- Measured behaviour is sandbox behaviour. tidehold runs only on the sandbox (product contract), so the sandbox decides.

## Branch A: the plan's current scope

### Flow

1. **Money moves once per trip, when the trip closes.** A `finalize_trip` due action (subject: trip) closes it in two cases:
   - at the trip's finalize time (below);
   - at once, when a whole-trip settlement is accepted. `_accept` schedules the action due now.
   
   8a's `settle_proposal` no longer sends the money of a whole-trip settlement. It keeps only its late-payment work (below). The 8a tests that assert a whole-trip command's `proposal_id` change to the finalize proposal and are listed in the commit.
2. **Snapshot.** In one transaction, under the trip row's lock (the lock that `record_cancellation`, `decide_proposal` and `check_trip` already take):
   - set `trips.closed_at`;
   - create the finalize proposal (kind `finalize`, subject `trip`, state `accepted`, `decided_by` `mandate`, `choice` `{"close": 0}`).
   
   The proposal's `parts.close[0].lines` freeze, per guest:
   - the payment ids it acts on;
   - the deposit refund;
   - the balance owed;
   - the balance credits, each with its settlement proposal;
   - the settlement proposals the snapshot counted.
   
   The money comes from every accepted or executed settlement of the trip:
   - **Deposit refund:** the chosen deposit `refund` lines, minus what the deposit already refunded.
   - **Balance owed:** the authorized amount, minus the chosen balance `refund` lines (the releases).
   - **Balance credits:** the chosen balance `credit` lines. They do not reduce the capture (8a: "the balance portion is still captured").
   
   No mandate check runs: the close returns nothing beyond what accepted settlements already returned under the mandate.
3. **Send.** The transaction commits, and the handler sends from the snapshot outside any lock. `money.service.execute` keeps its own transactions, so every command is stored before it is sent.
   - Per guest, `settlement.plan.settle_payment` turns the balance owed into a void, a partial capture or a capture, always with `final_capture`.
   - The deposit gets one refund command.
   - Every command records the finalize proposal as its authorizer.
   - Aggregating the accepted days into one refund and one closing command keeps them inside step 4's unique indexes: one refund per deposit, one capture or void per hold.
   - A rerun reads the snapshot, never `trips.guests()`, so a guest's current balance payment cannot replace the one the snapshot acts on.
4. **After `closed_at`:**
   - Accepting or rejecting a settlement is refused (`TRIP_CLOSED`). Rejecting would withdraw a cancellation and reopen the trip (`_withdraw`).
   - A new cancellation is stored and evaluated as in 8a, so the guest page can show it. Its proposal is always `asked` with the reason `AFTER_FINALIZE`, and it never sets `trips.cancelled_at`.
   - Such a proposal can be dismissed: `SETTLEMENT_NOT_DISMISSABLE` gains this exception.
   - Refund after capture stays out of scope (D16 ADR, D3).
5. **When the proposal is executed.** It becomes `executed` when every snapshot line has succeeded, has been refused and asked about, or was dismissed as settled outside tidehold.

### Asks still open at the close (Q6)

The plan says "an ask unanswered at finalize captures per the trip" (8b scope; D16 ADR D3: "No cancellation, no settlement"). The two kinds of ask need different readings:

- **Warning asks.** These are asks without a cancellation, and the plan's words apply directly. The trip is captured per its bound policy.
- **A settlement still `asked`.** The operator did cancel, but the outcome was outside the mandate or left a choice. Capturing in full overrides a cancellation the operator recorded, and AGENTS.md says that outside the mandate the system "stops and asks". This design does not choose. Options (Q6):
  - **(a) Capture per the trip.** The plan's wording read literally: the snapshot ignores the asked settlement, and the settlement then gets `AFTER_FINALIZE`.
  - **(b) Wait.** Finalize waits for the operator until expiry minus the margin, then does (a).
  - **(c) Capture only what no option disputes.** Per guest, the capture is the authorized amount minus the largest release among the asked settlement's options.
  
  The test `test_an_ask_unanswered_at_finalize_captures_per_the_trip` is written for the answer.
- **A whole-trip settlement still `asked`.** `trips.cancelled_at` is set when a whole-trip cancellation is recorded, even while its settlement is asked (`proposals.py:303`). So finalize is derived for every trip that is not closed and has a requested balance, `cancelled_at` or not. A whole-trip ask is not left holding until expiry. Its outcome follows Q6, and it has its own test: `test_a_whole_trip_ask_unanswered_at_finalize_is_closed`.

### Finalize time (`holds/schedule.py`)

- `finalize_at` is the earlier of:
  - the end of the last local trip day (8a's `day_window`);
  - the earliest `authorization_expires_at` among the trip's authorized balances, minus `FINALIZE_MARGIN`.
- This follows the plan's step 8 scope: "finalize before `authorization_expires_at`, not at the end of the last local trip day". A hold opened at UTC midnight of T-(29 − N) expires at the UTC end of the last trip day. West of UTC, finalize therefore lands inside the last local day: 08:00 local at UTC−10 with a 6 h margin. A cancellation later that day gets `AFTER_FINALIZE` (Q3).
- A null `authorization_expires_at` (PayPal answered without `expiration_time`) is read as `held_since` + 29 × 24 h. `held_since` is the first authorization, below.
- **Retry budget.** `FINALIZE_MARGIN` (6 h) is at least the worst case of one `finalize_trip` action: 8 attempts, about 61.5 min of backoff, and 8 leases of 15 min, about 3 h in all. A unit test derives that budget from `due_actions.MAX_ATTEMPTS`, `backoff` and `LEASES` rather than trusting the constant.
- **Retries alone do not bound the time:**
  - A failed `finalize_trip` is derived again, because the trip is still not executed.
  - `reconcile_payment` resends the unknown commands.
  
  The backstop is the hold-age guard below. No capture is sent at or after `authorization_expires_at`, or once the hold is 29 days old.
- **Unknown commands.** For each unknown command, the close schedules `reconcile_payment` again: an exhausted one is not open, so scheduling recreates it.
- **Waiting on an operator ask.** When the close waits only on an operator ask, the handler completes instead of raising `RetryLater`, so it does not use up its attempts. It is derived again 10 minutes later while the finalize proposal is `accepted`.
- **Scheduling.** `ensure_schedules` derives one `finalize_trip` per trip that is not closed and has a requested balance, due at `finalize_at`. The handler recomputes `finalize_at` and completes without closing when that time is still in the future. That is the case when a guest authorized later, giving an earlier expiry than the scheduled action assumed. It is then derived again with the new time.

### Reauthorization (per guest, in the send phase)

- **Two columns** replace one timestamp:
  - `payments.held_since` is the first authorization's time and is never overwritten. The 29-day age guard and the null-expiry fallback use it.
  - `payments.honor_from` is the time of the current authorization (`open` or `reauthorize`). The honor period runs from it. PayPal documents a new 3-day honor period for a reauthorization without extending the 29 days (deposit/balance ADR constraints; not measured).
- **Inside the honor period** (`now < honor_from + 3 days`), the capture goes to the current authorization with no reauthorization. PayPal allows a reauthorization only after the honor period (ADR constraints).
- **From `now ≥ honor_from + 3 days` on,** a `reauthorize` command comes first. It is for the owed amount (at most the authorized amount) and is authorized by the finalize proposal. Its outcome decides the next step:
  - **succeeded:** `authorization_id` is the new one, and the capture is recorded against it (`record_command` reads the target when it records);
  - **rejected:** the capture goes to the original authorization (the ADR's fallback), and `payment_events` keeps the refusal;
  - **unknown:** `RetryLater`, and `reconcile_payment` resends it under the same request id.
- **The decision is made again on every attempt,** with that attempt's `now`. A capture whose retry crosses the end of the honor period is reauthorized on the retry that finds it over.
- **Expiry.** `authorization_expires_at` keeps the earlier of the stored and the returned value, because a reauthorization does not extend the validity.
- **Hold-age guard.** Before sending, every capture checks `now < authorization_expires_at` and `now − held_since < 29 days`. Otherwise it records no command and opens a `capture_refused` ask with code `HOLD_EXPIRED`, subject `payment:<id>`.

### Refused capture: a second balance payment (D17)

- **Kind.** A capture refused by PayPal opens a `capture_refused` ask, with subject `command:<id>` (or `payment:<id>` for `HOLD_EXPIRED`).
  - It is a new kind. 8a's `money_refused` stays the settle-outside ask for a refused refund or void.
  - It shows the guest, the owed amount and PayPal's issue code.
- **Decisions.**
  - **Accept** (the operator acts, Q2): `decide_proposal` gains accept for this kind, with `parts.recovery[0]` holding the guest's owed amount and `choice` `{"recovery": 0}`. Accepting:
    1. creates the second balance payment: intent `capture`, amount = the owed amount, `guest_id` set;
    2. points `guests.balance_payment_id` at it, so the guest's link offers a checkout for that amount;
    3. records a `void` of the first payment's authorization, unless it has expired, so the guest is not held twice. The unique index allows it, because the refused capture is `rejected`.
  - **Dismiss:** the operator settled it outside tidehold.
- **Schema.**
  - `payments.guest_id` (new, nullable): payments without a guest (the sandbox smoke, money tests) stay valid. It has a workspace-consistent foreign key and is backfilled. `guests.balance_payment_id` keeps pointing at the guest's current balance payment.
  - `payments.intent` (`capture` | `authorize`, new; `authorize` only for a balance): `money.service._open` and `start_checkout` read it instead of the purpose.
- **Balance credit.** A guest's balance credit is written when the recovery payment captures. A named settlement sweep, `close.ensure_schedules`, finds captured recovery payments whose finalize line has an unwritten credit; the money layer cannot import settlement. If the ask is dismissed, no credit is written.
- **Timeline.** The trip timeline and `GET /failed-actions` read payments through `payments.guest_id`. The first payment's events (`capture_rejected`, `void_succeeded`) and the second payment's stay in the timeline (step 9's note).
- **Guest API.** It returns each payment's intent. Loading the JS SDK with that intent on the guest page belongs to step 9's 8b screens.
- **Refused refund.** It keeps 8a's settle-outside ask, because a checkout cannot pay a guest (Q1).

### Payments placed after the close

- **Balance checkouts.** A pending balance's checkout or test-card attempt is refused once `closed_at` is set (`TRIP_CLOSED`). The exception is a recovery payment.
- **Late-payment sweep.** 8a's sweep (`execute.ensure_schedules`, 30 days) widens from trips cancelled as a whole to closed trips. A balance authorized after the snapshot is voided, and a deposit captured after it is asked about (`late_payment`).

### A command left open after a refused resend

- `money.service.execute` already keeps such a command `unknown`, because its first send may have taken effect. 8b stamps `money_commands.resend_refused_at` there.
- A settlement sweep opens one `command_open` ask per such command, with subject `command:<id>`. Money cannot import settlement, so a sweep is needed. The question depends on the command's kind:
  - **capture, void or refund:** "PayPal refused a resend; the first send may have moved money; check PayPal". Dismissing it counts the line as settled outside tidehold.
  - **reauthorize:** nothing has been captured yet, so dismissing it would leave the balance uncollected. The ask asks whether to capture the original authorization (the D17 fallback) or settle outside. Whether capturing the original is safe after a reauthorization that may have succeeded is not known (inference; PayPal's docs do not say what happens to the original), so this is Q7.
- While the ask is open, the close treats that guest's line as waiting. The command stays open, so no second live command of its kind can be recorded.

### Data (migration 0012)

- **`payments`:**
  - `guest_id` (nullable, foreign key on `(workspace_id, guest_id)`);
  - `intent`, with the check `intent = 'capture' or purpose = 'balance'`;
  - `held_since`;
  - `honor_from`.
- **`money_commands.resend_refused_at`.**
- **`trips.closed_at`.**
- **`proposals`:**
  - `kind` adds `finalize` (subject `trip`), `capture_refused` and `command_open`;
  - the reason `AFTER_FINALIZE` joins `evaluate.Reason`.
- **`due_actions.kind`** adds `finalize_trip`, with a 900 s lease: up to three PayPal calls of 20 s per guest, 12 guests.
- **Test fake.** `fake_paypal.py` returns `expiration_time` 29 days after the injected clock rather than the fixed 2026-12-31, so finalize-time tests see a real expiry.

### Acceptance → tests (branch A)

| Plan acceptance item | Test |
|---|---|
| Outcome type: balance partial capture | `test_finalize_partially_captures_what_accepted_days_leave_owed` |
| Outcome type: balance capture | `test_finalize_captures_the_whole_balance_when_nothing_was_cancelled` |
| Capture refused → ask; the new checkout's payment and the first one's events stay in the timeline | `test_a_refused_capture_asks_and_its_new_checkout_keeps_both_payments_in_the_timeline` (also: the first authorization is voided) |
| A refused reauthorization captures against the original authorization | `test_a_refused_reauthorization_captures_the_original_authorization` |
| The last settlement action falls inside the validity for the longest allowed trip | unit `test_the_last_settlement_action_falls_inside_the_validity`: N = 1..10, time zones UTC−12 to UTC+14. It asserts that finalize plus the retry budget lands before expiry, and that finalize lands on the last local trip day |
| An ask unanswered at finalize captures per the trip | `test_an_ask_unanswered_at_finalize_captures_per_the_trip` (warning ask; the settlement case per Q6); `test_a_whole_trip_ask_unanswered_at_finalize_is_closed` |
| A cancellation after finalize asks | `test_a_cancellation_after_finalize_asks` (also: accept and reject refused, dismiss allowed, `cancelled_at` untouched) |
| Hold age < 29 days asserted | asserted in every capture test; `test_a_hold_29_days_old_is_never_captured` |
| Mutation: hold a 3-day trip so its last day is outside the validity → a test fails | Open the hold a day earlier (T-(30 − N)). The validity test fails on its "finalize lands on the last local day" assertion. Run against the 8b test alone, since step 6's `test_the_last_trip_day_falls_on_day_28_of_the_hold` fails too |
| Mutation: skip the reauthorization outside the honor period → a test fails | `test_a_capture_after_the_honor_period_is_reauthorized_first` |
| Developer review: no reauthorization inside the honor period; a retry that crosses it decides again | `test_a_capture_inside_the_honor_period_sends_no_reauthorization`; `test_a_capture_retried_past_the_honor_period_is_reauthorized_on_that_retry` |
| Both 8a and 8b: no settlement test touches a provider; evidence time < decision time | The settlement suite's raising provider stub stays installed; 8a's evidence-time assertion is unchanged |

Scope items with no acceptance line in the plan get a test too:

- `test_a_whole_trip_cancellation_captures_what_is_owed_at_once`
- `test_finalize_refunds_the_deposit_of_accepted_days_once`
- `test_a_cancellation_during_the_send_phase_is_after_finalize` (the snapshot race)
- `test_a_balance_credit_is_written_when_its_recovery_payment_captures`
- `test_a_command_refused_on_resend_becomes_an_ask`
- `test_a_balance_authorized_after_the_close_is_voided`
- `test_a_failed_action_of_the_first_balance_payment_still_shows` (step 9's note)

Docs changed with the code:

- `docs/threat-model.md`: the validity row names its test, and a row covers the recovery checkout on the guest link.
- README: finalize and the recovery checkout.
- 8a's whole-trip tests that change: listed in the commit, with test counts in `quality-lock.json`.

## Branch B: the sandbox refuses reauthorization and late captures

The deposit/balance ADR's first revocation trigger names two directions: "move evaluations inside the honor period or ask the operator earlier". Both change a decided ADR (D11), so the developer picks one in stage 2 (Q5). Nothing of B is built before that.

Either way, these parts of A stay:

- the close (snapshot, then send);
- the aggregated deposit refund;
- the second balance payment after a refused capture;
- the open-command ask;
- the after-finalize ask;
- `payments.guest_id`.

### B1: finalize inside the honor period

- **Hold timing.** The balance hold opens 3 days minus the retry budget before the trip's finalize time. D11's T-(29 − N) becomes "finalize − 3 days + margin". Every capture is then inside the honor period, and no reauthorization exists. No authorization can be made before that window, because the payment does not exist yet.
- **Trip length.** If the hold must be placed before the first trip day, trips of at most 2 days are possible: N ≤ 3 − margin/24 h. That is the ADR's rejected option E (D11 C), which excludes liveaboards. Allowing longer trips needs guests to approve during the trip, which a liveaboard without connectivity cannot do. Sub-decision: `MAX_TRIP_DAYS` 2, or approval on site up to 10 days.
- **8a's unauthorized-guest ask.** 8a's rule, "a guest without an authorization by the first trip day becomes an ask" (`watch._guest_asks`; test `test_a_guest_without_an_authorization_on_the_first_trip_day_is_asked_about`), depends on that sub-decision:
  - with `MAX_TRIP_DAYS` 2 it stays as it is, because the window opens before the first day;
  - with approval on site it becomes 12 h after the window opens, since the window may be shorter than a day.
- **Scope (8b), replacing the current text:** "finalize on the last trip day inside the honor period of every hold (one capture with final_capture, or a void); a refused capture or refund becomes an ask with a new checkout as a second payment of the guest; a command open after a refused resend becomes an ask; an ask unanswered at finalize captures per the trip; a cancellation after finalize asks."
- **Acceptance (8b):** "tests per outcome type (balance partial capture, capture); capture refused → ask, both payments in the timeline; every capture falls inside the honor period for the longest allowed trip in every time zone; an ask unanswered at finalize captures per the trip; a cancellation after finalize asks; asserting hold age < 3 days at capture; mutations: open the hold one day earlier → the honor test fails; let a capture run past the honor period → a test fails."
- **Code that changes with it (step 6):** `holds.schedule.balance_opens_at`, `MAX_TRIP_DAYS`, `test_the_balance_hold_opens_at_t_minus_29_minus_n`, the "day 28" parametrized test, and its mutation.

### B2: ask the operator earlier, capture inside the honor period, refund later

- **Timing.** The hold still opens at T-(29 − N). On hold day 2, inside the honor period, the operator is asked to confirm each guest's capture of the full balance. Confirmed captures move money then, weeks before the trip. Weather cancellations afterwards become refunds of the captured balance.
- **Unconfirmed holds.** A hold the operator did not confirm by the end of the honor period is voided. The guest then has no payment, so an `unauthorized_guest`-style ask opens: collect outside tidehold or send a new checkout.
- **Cost.** Guests pay before the weather is known. The ADR rejected this as option D ("refunds lose fees"). Refund after capture comes into scope, so these change:
  - D16 ADR D3;
  - the one-refund-per-payment index (several partial refunds, bounded by the captured amount);
  - `commands.NEEDS_STATE["refund"]`, which today requires a captured deposit;
  - the plan's Goal ("void or partial capture").
- **Scope (8b), replacing the current text:** "on hold day 2 an operator ask confirms each guest's balance capture (final_capture); a confirmed capture runs inside the honor period; a later accepted cancellation refunds the captured balance per the policy; refused captures and refunds become asks (new checkout for a capture); a command open after a refused resend becomes an ask; a hold not confirmed by the end of the honor period is voided and asked about."
- **Acceptance (8b):** "tests per outcome type (capture, refund after capture, void of an unconfirmed hold); no capture after the honor period; refunds never exceed the captured amount across several cancellations; mutations: capture after the honor period → a test fails; let two refunds exceed the captured amount → a test fails."

### Plan rows, ADRs and docs that change under B

- **Plan:**
  - the Premises line on PayPal authorization (it gets the measured result);
  - the Invariants row "Every capture happens within the authorization's validity; outside the honor period a reauthorization precedes capture";
  - Scope "Don't" (trips that cannot settle inside one authorization's validity);
  - D11 (a new decision entry with date and decider);
  - step 6 Scope and Acceptance (B1);
  - step 8 Scope (reauthorization and its fallback);
  - 8b Scope and Acceptance (the texts above);
  - step 4's reauthorization check (its result);
  - the Goal line (B2);
  - the 8b Progress row.
- **`docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`:**
  - D11 and the Failures line are superseded by a new ADR. The old one gets `status: superseded` and a review note naming the day-4 evidence.
  - Expected Outcome ("a test fails if the last action of a 10-day trip falls after day 28") and Followup.
  - B2 also changes D16 ADR D3 ("refund after capture is out of scope").
- **Other docs:**
  - `AGENTS.md`: the product contract line on the balance hold (T-(29 − N), reauthorization, 10 days);
  - `requirements.md`: R5 and the PayPal interface row;
  - `README.md`: the reauthorization mention;
  - `docs/threat-model.md`: the surface list that names reauthorize, and the validity row;
  - `step9-design.md`: "Waits for 8b" (no reauthorized state);
  - the runtime ADR: its context line naming T-(29 − N) gets a review note.

## Questions for the developer (stage 2)

- **Q1 Refused refund.** The plan says "a refused capture or refund becomes an ask with a new checkout". This design gives a new checkout only to a refused capture. A refused refund keeps 8a's settle-outside ask, since a checkout cannot pay a guest. Confirm.
- **Q2 Who creates the recovery payment.** The second balance payment is created when the operator accepts the `capture_refused` ask, not automatically at the refusal. Confirm.
- **Q3 West of UTC.** Finalize lands inside the last local day (UTC−10: 08:00 with a 6 h margin), so a cancellation later that day is after finalize. The plan decided "before `authorization_expires_at`". The alternative is opening holds at T-(28 − N), which changes step 6 and the contract line. Recommended: keep the plan's rule.
- **Q4 Day-4 rows the ADR does not cover.** What to do with a reauthorization that succeeds whose capture is refused: (i) when the original's capture works, (ii) when it is refused too, and whether that counts as B.
- **Q5 Under B.** B1 (finalize inside the honor period; trips ≤ 2 days, or approval on site) or B2 (operator-confirmed capture inside the honor period, refunds after). Lean: B1 with approval on site. It keeps the balance held rather than charged, which is the ADR's reason for holding at all. Its cost is that guests approve during multi-day trips.
- **Q6 A settlement still asked at finalize.** Options (a), (b) or (c) in "Asks still open at the close".
- **Q7 An open reauthorization after a refused resend.** May the close capture the original authorization, or does it always ask?
- **Q8 Confirming the late capture.** Should step 4's check add a capture near day 27 of a card hold, before the 2026-11-17 expiry, with its own trigger? Changing `day4` so that it resends an `unknown` step is step 4 code, not 8b.

## Out of 8b

- Console screens for finalize, captures, reauthorization and the recovery checkout, and the SDK intent on the guest page (step 9).
- A live sandbox run of finalize. It needs step 10's deploy, and the live demo cannot show a reauthorization because holds are voided nightly (deposit/balance ADR, Followup).
- Refund after capture (D16 ADR, D3), except under B2.
- Fixing `sandbox.py`'s `day4` rerun (step 4).

## Design review (2026-10-23)

The developer reviewed it first, then an independent reviewer who had not taken part in the design. The reviewer read it against the plan, the ADRs, the 8a design and the code, read-only. The citations of findings 2, 4, 7 and 17 were checked against the code before changing anything.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| D | Reauthorizing inside the honor period (`honor_from + 3 days − HONOR_MARGIN`) contradicts "reauthorize only after the honor period" | developer | Changed: capture directly inside it, reauthorize only from `honor_from + 3 days`, decided again on every attempt; two tests added |
| 1 | `closed_at` set after the sends lets a cancellation or acceptance during them go uncovered and unflagged; the close cannot hold the trip lock while `execute` sends | HIGH | Changed: snapshot under the lock (`closed_at` plus frozen lines), then send from it; the race test is added |
| 2 | Day-4 "unknown → rerun" cannot work: `_step` skips recorded `unknown` steps, and C-R never runs after an unknown R; C-R and C-O unknown were missing | HIGH | Changed: the table says that a rerun needs a step 4 fix, and covers any unknown. The fix is not made here (step boundary) and is reported to the developer before the check runs |
| 3 | One `authorized_at` cannot serve both the honor period (latest) and the 29-day age and expiry fallback (first) | HIGH | Changed: `held_since` and `honor_from` |
| 4 | `cancelled_at` is set on recording a whole-trip cancellation, so an asked whole-trip settlement got no finalize and the hold stayed until expiry | HIGH | Changed: finalize is derived for every trip not closed; outcome per Q6; a test is added |
| 5 | D3 ("no cancellation, no settlement") was applied to settlement asks, where the operator did cancel; capturing in full overrides it | MED | Changed into a question (Q6) with three options; warning asks keep the plan's reading |
| 6 | The whole-trip close ran under `settle_proposal`'s 600 s lease with up to 720 s of calls | MED | Changed: both closes run as `finalize_trip` (900 s) |
| 7 | Accepting a `money_refused` ask is impossible today (`ONLY_A_SETTLEMENT_IS_ACCEPTED`, `choice` required on `accepted`); `HOLD_EXPIRED` has no command subject | MED | Changed: a new kind `capture_refused` with its own `parts` and `choice`, accepted by `decide_proposal`; subject `payment:<id>` for `HOLD_EXPIRED` |
| 8 | The recovery checkout charges while the first authorization still holds the guest | MED | Changed: accepting voids the first authorization unless it has expired |
| 9 | `trips.guests()` sees only the current balance payment; the recovery's balance credit had no owner | MED | Changed: the close acts from its snapshot; `close.ensure_schedules` writes the credit |
| 10 | Dismissing an open `reauthorize` leaves the balance silently uncollected | MED | Changed: a kind-specific question; the safe fallback is Q7 |
| 11 | `HONOR_MARGIN` guaranteed refused reauthorizations in its window, and had no size | MED | Same as D: removed |
| 12 | Mutation 1 may fail only step 6's test | MED | Changed: the validity test also asserts that finalize lands on the last local day; the mutation is run against the 8b test alone |
| 13 | The table row "R accepted, C-R and C-O refused → B" goes beyond the trigger's wording; day 4 is not day 28; refusals need an issue code | MED | Changed: that row goes to Q4; Q8 asks for a near-day-27 capture; refusal classification added |
| 14 | The retry budget is not a bound: failed actions are recreated, `RetryLater` on an ask spends attempts, an exhausted `reconcile_payment` is not recreated | MED | Changed: the hold-age guard is the backstop; waiting on an ask completes instead of retrying; the close reschedules `reconcile_payment` |
| 15 | A pending balance can be authorized after the close and nothing settles it | MED | Changed: balance checkouts are refused after `closed_at` (except recovery); the late-payment sweep covers closed trips |
| 16 | The B list missed README, step 9 design, threat-model surface list, Goal, 8a's guest ask; B1 is the ADR's option E; B2 also hits `NEEDS_STATE`, leaves an unconfirmed guest without a next step, and its mutation said "day 4" | MED | Changed: all added to B |
| 17 | After the close, reject would reopen the trip, a whole-trip cancellation would set `cancelled_at`, and dismiss is refused for settlements | LOW | Changed: reject refused, `cancelled_at` untouched, dismiss allowed for `AFTER_FINALIZE` |
| 18 | 0012 constraints: `guest_id` must be nullable; intent check; the finalize proposal's `choice` and `decided_by` | LOW | Changed: specified in Data and Flow |
| 19 | The fake returns a fixed expiry | LOW | Changed: the expiry is relative to the injected clock |
| 20 | The threat-model row, README and 8a test changes were not listed | LOW | Changed: listed under Acceptance |
| 21 | Step 4's acceptance asks for a PayPal-account hold that was never placed | LOW | Recorded: step 4's row stays open on that item unless the developer waives it |
