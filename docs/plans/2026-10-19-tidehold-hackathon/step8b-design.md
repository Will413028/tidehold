# Step 8b design: refunds and credit at decision, unpaid balances, PayPal state

Plan step 8b, "Scope", "Acceptance" (`../2026-10-19-tidehold-hackathon.md`); ADR `docs/adr/2026-10-23-balance-paid-before-trip-weather-refunds.md` D1, D2, D4 and the amendment (late and unpaid payments); D21; the settlement ADR D3 as amended 2026-10-23. Written 2026-10-24 at 65c1df7, before building.

Premise: the code changed here (`settlement/execute.py`, `proposals.py`, `evaluate.py`, `watch.py`, `guest.py`) is not scheduled to be rewritten or deleted after 8b; step 8c removes only the hold paths in `money/` and `settlement/plan.py`, and step 9 builds screens on this API. So this is the end state of settlement, not a bridge.

## The plan's text does not add up, and what this design does instead

The plan says (a) the base is the gross captured amount of the deposit or balance, (b) the balance order is the balance minus the refund lines accepted settlements gave this guest's balance, and (c) a payment captured after a settlement was evaluated gets a proposal of its own. Read together:

- With (a), a balance not yet paid has base 0, so every refund line on it is 0 and (b) subtracts nothing: the guest pays the full balance, and then (c) refunds the cancelled day. That is "pays, then gets refunded" with PayPal's fee lost, which (b) exists to prevent (ADR amendment: "a cancelled day is never charged and then refunded, and no PayPal fee is lost on it").
- With (b) applied, a balance captured after a refunded day holds less than the balance, so (a)'s base for a second cancellation is the reduced amount, and the second day's share is computed on the wrong base (3-day trip, balance 300: day 1 refunds 100 and the guest pays 200; day 2 then computes 1/3 of 200 = 66, not 100).

This design takes **the payment's own amount** (`payments.amount_minor`, what the guest owes on it at invitation) as the base for both purposes, whether or not it is captured yet, and nets later settlements only through what earlier ones returned (unchanged from 8a, review finding 2 of the D18 rewrite). Evaluation then never depends on whether a payment is captured. What a line does is decided when it executes, from the payment's state under its row lock:

| Line | Payment captured | Payment not captured, no attempt sent | Payment not captured, an attempt sent (in flight) |
|---|---|---|---|
| refund | a `refund` command for the line's amount | a **waiver**: the amount due on the payment drops by the line's amount; unsent checkout attempts are closed | wait; when the attempt ends: captured → refund, not placed → waiver |
| credit | a `credits` row | wait until it is captured ("credit lines are charged and credited", D21) | wait, as for refund |
| none, or amount 0 | nothing | nothing | nothing |

Consequences, each checked against D21:

- (b) holds exactly: the order is created for the amount due, `amount_minor − waived`, so a refunded day is never charged.
- (c) has no case left among the guests a settlement lists: every listed payment has its line, computed on its full amount and through the mandate check at decision. A payment captured later executes the same line (refund or credit). The new proposal per late payment, the `payment:<id>` subject, the "original's policy version" re-evaluation and its mutation are not built. Guests invited after a cancellation are not in its lines and owe the operator's full price; the operator invited them after cancelling.
- (3), the trip lock in `complete_checkout`: its necessity condition is that a capture could land between evaluation and execution and escape the settlement. Here a waiver closes every unsent attempt under the payment's row lock, and an attempt already sent is waited for, so no capture escapes; the lock is not taken. A whole-trip cancellation still refuses new checkouts (`TRIP_CANCELLED`, 8a), and `test_a_checkout_completing_during_a_whole_trip_cancellation_is_refused_or_settled` proves the race with two connections.
- The mandate counts waived amounts like refunds: the operator ends with the same money either way.

**For the developer to confirm (deviates from the plan's wording, not from D21's intent):** the payment's own amount as the base; no own proposal for late payments; no trip lock in `complete_checkout`; credit on an unpaid payment written when it is captured, not at acceptance.

## Data (migration 0016)

- `payment_waivers (payment_id, proposal_id, amount_minor > 0, created_at, unique (proposal_id, payment_id))`: one per settlement and payment, so a rerun waives once. Append-only (trigger, like `credits`).
- `payments.waived_minor` (default 0, `waived_minor <= amount_minor`), kept in the same transaction as each waiver under the payment's row lock; the table is the record, the column what `open_attempt` and the guest page read.
- `guests.released_at` and `guests.released_by` (the `unpaid_balance` ask that released it).
- Proposals kind check: `unpaid_balance` replaces `unauthorized_guest` (existing rows renamed, same `guest:<id>` subject), `late_payment` goes (its rows and their `draft_ask` actions deleted: local development databases only, no deployment), `command_open` added.
- Nothing else changes in the money schema: `refunded_minor <= captured_minor` and the one-refund-per-proposal index stay.

## Money (`money/commands.py`, `money/service.py`)

- `waive(conn, payment_id, *, amount_minor, proposal_id) -> Waived` under the payment's row lock: closes the payment's unsent `open` attempts (`status = 'rejected'`, `AMOUNT_DUE_CHANGED`; each command row is locked by the update, so it serialises with `claim`); then, if an `open` attempt is sent and still open, returns `in_flight` and waives nothing; if the payment is captured, returns `captured`; otherwise inserts the waiver (`on conflict do nothing`: `already`), raises `waived_minor` (capped at `amount_minor`, so a waiver never makes the amount due negative) and appends a `waived` payment event.
- `open_attempt` creates the attempt for `amount_minor − waived_minor` and refuses `NOTHING_DUE` when that is 0.
- `_placed_by` already requires the captured amount to equal the attempt's amount.

## Evaluation and acceptance (`settlement/proposals.py`, `evaluate.py`)

- `_money`: both bases are the payment's `amount_minor` (a guest invited before migration 0015 has no balance payment: base 0). `GuestMoney`'s comment and the line's `base_minor` say "what the guest owes on the payment".
- Released guests are left out of `trips.guests` for evaluation (a `released_at is null` filter in the settlement's own query), so they get no lines, count toward no cap and are never settled again.
- `_accept`, inside the decision's transaction (trip row locked): per guest and purpose of the chosen options, a refund line on a payment not captured is waived at once (unsent attempts closed, so the guest page's next checkout is for the reduced amount); a credit line on a captured payment is written (`on conflict do nothing`). Anything else left (a refund of a captured payment, a line on a payment in flight, a credit on an unpaid payment) schedules `settle_proposal`, day and whole trip alike. When nothing is left, the proposal is `executed` at once.

## Execution (`settlement/execute.py`, the `settle_proposal` handler)

- Per guest of the proposal's lines, per purpose, the line's move from the table above. Refunds go through 8a's and step 4's machinery unchanged (`_send`, `_record`, `_being_read`, `_final`, `_refused`).
- A move on a payment in flight or a credit on an unpaid payment leaves the proposal `accepted`. It is `executed` when every move is final.
- The schedules sweep (`ensure_schedules`, replacing the late-payment sweep) runs a settlement again when an `accepted` one has a move that can act now: its payment was captured, or was not placed and is no longer in flight. A move that must still wait does not schedule, so a guest who never pays costs nothing; a released guest's moves are dropped (its proposal is executed without them).
- Removed: the whole-trip void, `_released`, `_placed_late`, the late-hold void, `_late_deposit` and the `late_payment` ask, `LATE_SWEEP`, the import of `settlement.plan` (step 8c rewrites or deletes `plan.py`). A balance still `authorized` (pre-0015 development data only) is treated as in flight: never refunded, never waived.

## Asks

- **`unpaid_balance`** (from `check_trip`): from the guest's `balance_due_at` on, a guest not released whose balance is not captured and still owes something (`amount_minor − waived_minor > 0`). Facts: guest, amount due, due date. Answers through the decision route: `dismiss` is "wait" (the link stays open; a later payment lands as any other), and a new `release` answer marks the guest released and waives what is left of its balance (the waiver names the ask), so no checkout can start for it and no money moves. Release is refused while a balance attempt is in flight (`BALANCE_PAYMENT_IN_FLIGHT`) or after the balance is captured (`BALANCE_PAID`). The ask stays open until answered, also if the guest pays meanwhile.
- **`check_trip` window:** see review finding 3 below (superseded).
- **`money_refused`:** a refusal for `REFUND_WINDOW_CLOSED` says the capture is older than PayPal's 180-day window and suggests refunding the guest another way or recording a credit. The other codes keep 8a's text.
- **`command_open`** (from a settlement sweep, `settlement.watch`, since `money` cannot import `settlement`): a command still open that a read could not decide: PayPal refused its resend (`resend_refused_at`), or its payment's last `reconcile_payment` failed after reading it (`read_at >= created_at` of that action). Facts: the guest, purpose, kind and amount, the error code and PayPal's last answer stored on the command (order, capture or refund id and status). Answer: `dismiss`, after the operator checked PayPal. The ask does not close the command; step 10's nightly reads keep reading it.

## Guest view (`settlement/guest.py`, `holds/http.py`)

- Each outcome gains `status` from the payments: `refunded`, `refund_processing`, `refund_refused`, `not_charged` (waived), `credited`, `waiting_for_payment`, or `none`. Amounts stay the stored lines' (step 9's rule).
- The guest's payment view gains `due_minor` (amount − waived) and `released`; a checkout for a released guest is refused (`GUEST_RELEASED`).
- Screens stay step 9's, except where this step makes them wrong: `GuestSettlements.tsx` maps `refund:balance` to "refunded" (a balance is no longer released), `proposal-view.ts` names `unpaid_balance` instead of `unauthorized_guest`, and the e2e demo path asserts the balance refund ("Ana: refund USD 150.00", "Your balance: USD 150.00 refunded").

## Tests

Plan acceptance names, kept: per outcome type at decision time for a day and a whole-trip cancellation (deposit refund, balance refund, deposit credit, balance credit); `test_two_day_cancellations_refund_one_payment_twice_never_above_its_capture`; `test_the_rest_of_a_trip_after_an_executed_day_refund_returns_the_full_remainder`; `test_an_unpaid_balance_asks_at_its_due_date_and_not_before`; `test_the_balance_due_after_a_refunded_day_is_reduced_by_that_refund`; `test_a_checkout_completing_during_a_whole_trip_cancellation_is_refused_or_settled` (two connections); `test_a_released_guest_is_left_out_of_later_settlements`; `test_a_refund_past_the_window_asks_with_its_reason`; `test_an_unknown_refund_found_at_paypal_is_not_resent`; `test_a_refused_resend_that_paypal_cannot_settle_becomes_an_ask`; `test_a_cancellation_recorded_after_the_trip_is_settled_like_any_other`; `test_an_ask_stays_open_until_the_operator_answers`.

Replaced, if the developer confirms the base: `test_a_payment_captured_after_evaluation_is_settled_by_its_own_proposal_within_the_mandate` becomes `test_a_payment_captured_after_its_decision_is_settled_by_that_decision` (a balance in flight at acceptance is refunded once captured; one not yet paid with a credit line is credited when paid), plus `test_waived_amounts_count_toward_the_mandate`.

New for the waiver: `test_a_waiver_closes_unsent_attempts_and_waits_for_one_in_flight` (money), `test_nothing_due_refuses_a_checkout`.

Mutations (plan's, adapted): settle on the warning alone → `test_a_warning_without_a_cancellation_never_moves_money` fails; base = captured − refunded again → the remainder test fails; drop the netting against earlier settlements → the remainder test fails; open the unpaid ask before the due date → its test fails; charge the full balance after a refunded day (`open_attempt` ignores `waived_minor`) → the reduced-balance test fails; include released guests in evaluation → the released test fails. The plan's "late payment against the current policy version" mutation has no code to act on and goes with the own proposal. Added: waive without closing unsent attempts → the checkout race test fails.

Tests changed (listed in the commit): the plan's list; `_balances_held` and every use of it go, since nothing holds a balance any more.

## Units (sequential: each writes settlement state the next reads)

1. Migration 0016 and the waiver in `money` with its tests.
2. Evaluation base, acceptance and `settle_proposal` (refund, waiver, credit, sweep), the 8a hold paths removed; `test_settlement.py` rewritten.
3. The unpaid-balance ask, release and the `check_trip` window.
4. `command_open` and the window text of `money_refused`.
5. Guest view, guest payment fields, the web words and the e2e assertions.
6. Docs: `docs/threat-model.md` (waivers, release, command_open rows), `AGENTS.md` product contract ("or as a reduction of what is still unpaid"), `requirements.md` where R9 and R12 name the hold, plan progress and `docs/evidence.md`.

Consumers: `rg -n "unauthorized_guest|late_payment|_late_deposit|_released|base_minor|settle_payment|balance_state|amount_minor" backend/src web/src web/e2e`.

## Built (2026-10-24)

What changed from the sections above while building:

- `settlement/moves.py` holds the move table (`move`), the waiver and credit writes, and `run_without_paypal`, which `_accept` runs inside the decision's transaction; `execute._settle_line` runs every move. `add_months` moved there, since credits are written there.
- An operator's answer that unblocks a settlement (release, resolve) brings its pending `settle_proposal` forward with the new `due_actions.wake`; `schedule` leaves an open action as it is, so the answer would otherwise wait out a retry's backoff.
- `decide_proposal` takes its answers from a table (`ANSWERS`: which kind takes accept, reject, dismiss, release or resolve), and a refused answer is `<KIND>_TAKES_NO_<ANSWER>` (8a's `SETTLEMENT_NOT_DISMISSABLE` and `ONLY_A_SETTLEMENT_IS_*` become `SETTLEMENT_TAKES_NO_DISMISS` and the like; nothing outside the backend read them).
- `reconcile_due` no longer backs off a command PayPal refused to resend (`held`): the `command_open` sweep asks the operator whatever the action's state.
- `settlement/plan.py` is no longer imported; step 8c deletes or rewrites it with its unit tests.

## Design review at start (2026-10-24)

A fresh reviewer read this design against the plan, the three ADRs, `AGENTS.md`, the threat model and the code at 65c1df7 (read-only). 10 findings: 6 changed, 2 recorded, 2 for the developer.

| # | Finding | Disposition |
|---|---|---|
| 1 | The late-payment proposal and the trip lock are decided in the D18 ADR's amendment, not only the plan; unit 6 did not amend the ADR | Changed: unit 6 adds an amendment to the D18 ADR and rewrites D21's conclusion in the plan, once the developer confirms the four points |
| 2 | `command_open` with only `dismiss` never converges: the command stays open and its settlement `accepted` forever (wiki rule: an unknown refund needs a path that converges) | Changed: the operator answers after checking PayPal: `found` (PayPal shows the refund: the command succeeds, recorded as the operator's answer in its result and the payment events) or `not_found` (the command is rejected, and its settlement opens 8a's `money_refused` ask). For an `open` only `not_found` is accepted (a capture without its id cannot be refunded later); `found` is refused (`OPEN_NEEDS_PAYPAL_READ`). `reconcile_due` no longer backs off a held command. The ask's condition (`watch.UNDECIDED`, SQL) is the same test as `execute._being_read` (Python), written twice: the sweep reads every command, the settlement one |
| 3 | `check_trip` polled for up to 120 days, with an hourly special case, for one known instant | Changed: before the warning window `ensure_schedules` schedules a `check_trip` only once an unpaid balance's ask time (`unpaid_ask_at`) has come; schedules are derived on every tick, so nothing is scheduled ahead (a guest invited later may be due sooner than one already scheduled). The 10-minute period runs only inside the warning window. No hourly case |
| 4 | Special cases for pre-0015 data (authorized balances treated as in flight; guests without a balance payment at base 0), while the same design deletes `late_payment` rows for having no deployment | Changed: 0016 gives every guest without a balance payment one (`capture`, the trip's balance) and turns a pending `authorize` balance into `capture`; a balance still `authorized` (a placed hold in a development database) makes the settlement raise `PAYMENT_HELD` instead of waiting silently |
| 5 | `waive` capped silently while the waiver row kept the full amount | Changed: no cap; a waiver above what is still due is refused before anything is stored (`WAIVER_EXCEEDS_DUE`) and asks like a refund refused before PayPal. Netting through `returned` makes it unreachable by construction; the refusal guards the invariant |
| 6 | The move table implemented twice (acceptance and handler) | Changed: one `_move(line, payment)` decides the move; `_accept` and `settle_proposal` both execute it, `_accept` only the moves that need no PayPal call |
| 7 | A credit on a payment never paid leaves its settlement `accepted` with no end | Recorded: `accepted` is the true state (money still to move); the guest's `unpaid_balance` ask is the operator's way out (release drops the guest's moves and executes the settlement), and the sweep reads only `accepted` settlements, so the cost is bounded by their count. Revisit if step 9's console shows settlements accepted past their trip's end |
| 8 | A guest invited after the due date is asked about at the next check, before it could pay | For the developer (below) |
| 9 | The second half of plan item (3), "whether a guest has any uncancelled trip day is computed on each call", was not addressed; a guest can pay in full while a settlement is still asked | Recorded: the second half is covered by waivers (a fully refunded payment owes nothing: `NOTHING_DUE`) and needs no per-call check; a payment made while a settlement is asked is captured in full and refunded at acceptance, losing PayPal's fee, accepted as the cost of letting the operator decide |
| 10 | `base_minor` keeps a field whose only reader goes | Recorded: kept in the stored lines as the amount each line was computed from (the proposal is the decision record; step 9's console may show "of USD X") |

Decided by the developer (2026-10-24):

- The four points above (base, no own proposal, no trip lock, credit when captured), with the ADR amendment of finding 1: confirmed.
- Finding 8, a guest invited after its due date, of (a) ask at the next check, (b) ask 24 hours after the invitation, (c) ask two days before the trip: (b). The ask opens at `max(balance_due_at, invited_at + 24 h)` (`holds.schedule.unpaid_ask_at`); the due date itself is unchanged.

Must not change: 8a's evaluation reasons, warning matching, mandate caps and the proposals unique key; D16 (no cancellation, no settlement); step 4's stored-before-sent protocol, refund bound, window and reads.

## Closing reviews (2026-10-24)

Two fresh reviewers read the uncommitted diff against 1d90884: a correctness reviewer (adversarial prompt, contract and diff only; it reproduced its HIGH with a probe test) and the design-review prompt. One ledger:

| # | Finding | Severity | Disposition |
|---|---|---|---|
| C1 | A refund line waived at decision was refunded too when the guest paid the reduced balance before the settlement ran (the move was decided from the payment's state alone) | HIGH | Fixed: a refund line this settlement waived is done (`moves.waived`, in `_settle_line` and in the sweep's test); `test_a_balance_paid_after_its_day_was_waived_is_not_refunded_for_that_day`; mutation: drop the check → that test fails |
| C2 | A `command_open` ask whose command a read settled afterwards could never be answered (`MONEY_COMMAND_NOT_OPEN`) | MED | Fixed: the answer is taken as it stands and the ask closes |
| C3 / D7 | The guest page showed the full balance while checkout charged the amount due, offered payment to a released guest, and worded a waived balance "refunded" | MED | Fixed: it shows the amount due, offers nothing when nothing is due or the guest is released, and words outcomes from `status` ("taken off what you owe", "refund in progress", "once you pay"); unit tests |
| C4 | Paths without tests: `resolve: found`, a waiver refused above what is due, a release refused while a payment is at PayPal, `due.wake`; the design's named waiver test missing | MED | Fixed in part: `test_a_refund_the_operator_finds_at_paypal_is_recorded_and_its_settlement_ends` (found, and `wake` through it); the waiver's closing and waiting are proven by `test_a_checkout_completing_during_a_whole_trip_cancellation_is_refused_or_settled`, which replaces the design's named test. Recorded: `WAIVER_EXCEEDS_DUE` and `BALANCE_PAYMENT_IN_FLIGHT` have no test (the first is unreachable by netting; the second needs a release racing a claimed attempt); add one if either is seen on the console of step 9 |
| C5 | A release while a settlement is running can leave that settlement `accepted` (the wake meets a running action) | LOW | Recorded: the settlement is finished by the next answer or payment that wakes it; revisit if step 9's console shows a settlement accepted with only released lines left |
| C6 | A waiver refused above what is due made the sweep schedule its settlement every tick | LOW | Fixed: a refused waiver's open `money_refused` ask counts as done in the sweep |
| C7 / D6 | Releasing a guest dropped its decided deposit lines too | LOW / MED | Fixed: only the released guest's balance lines are dropped; its deposit lines move as decided |
| C8 | Plan progress and `docs/evidence.md` not updated | LOW | Fixed with the commit |
| D1 | An `open` command PayPal refused to resend, found captured at PayPal, cannot be answered `found`, so its waivers and a release wait for ever | MED | Recorded: answering `found` would need the capture id from the operator and a read of it (PayPal's `GET /v2/payments/captures/{id}`); a refused resend of a payment attempt has not been seen in the sandbox. Trigger: a `command_open` ask on an `open` seen on step 9's console or step 10's nightly log |
| D2 | The design claimed the `command_open` condition shares `_being_read`'s query; it is written twice | — | Changed: finding 2's row now says what was built |
| D3 | What a line has done is derived in three places (`moves.move`, the sweep's `_DONE`, the guest view's `status`) from three tables | MED | Recorded: one row per line with its own state would make them one; not built for the hackathon. Trigger: a fifth move kind, or the three disagreeing in a test |
| D4 | The settlement sweep reads every accepted settlement each tick, with no bound (8a's had 30 days) | MED | Recorded: waking from the payment's change would need the payments layer to find settlements by payment (their lines are JSON in `proposals.parts`). Cost: one read per accepted settlement per tick. Trigger: a worker tick measured above one second on the demo (step 10) |
| D5 | Before the warning window `check_trip` is derived every tick for every trip not over, though `wake` now allows scheduling at `unpaid_ask_at` | MED | Recorded: deriving keeps one source of truth with no invitation hook; cost: two reads per trip not over per tick. Same trigger as D4 |

