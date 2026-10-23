# Step 6 (D18) design: the balance payable from invitation, due before the trip

Plan step 6, "Scope (D18, rewritten 2026-10-23)" and "Acceptance (D18)" (`../2026-10-19-tidehold-hackathon.md`); ADR `docs/adr/2026-10-23-balance-paid-before-trip-weather-refunds.md` D1 and the amendment (bounds); D20. Written 2026-10-23 at 4545866, before building; reviewed at start and before commit (findings at the end).

Step 6 changes when and how a balance is created; it does not change settlement (8b) or remove the hold code (8c). `make check` stays green at every commit.

## Decided by the developer before building (2026-10-23)

- **8a's tests keep exercising the hold.** With balances created as `capture`, 28 tests of `test_settlement.py` fail at its `_guest` helper, which asserts an `authorized` balance (measured: only the intent changed). 8a's code still voids and releases authorized holds until 8b and 8c rewrite it, so a helper `_balances_held` sets the trip's unpaid balances to `intent = 'authorize'` before they are paid: in `_guest`, before Ben's balance in `test_a_refund_refused_when_recorded_asks_and_the_other_guests_are_refunded`, and in `test_payments_placed_while_the_trip_was_being_cancelled_are_released_or_asked_about` and `test_a_payment_that_lands_after_the_settlement_ran_is_still_released` (review finding 1). Production creates every new balance as `capture`. The e2e demo path asserts what 8a does with a captured balance: until 8b, a weather cancellation leaves a captured balance untouched (`proposals._money` counts only an `authorized` balance), so the decision panel shows "Ana: nothing" under Balance and the guest outcome "Your balance: no change"; 8b adds the balance refund (review finding 8).
- **No backfill.** Migration 0015 creates no balance payment for guests invited before it whose trip's balance had not opened. There is no deployment yet (step 10); only local development databases have such guests. `guests.balance_payment_id` stays nullable, and the code keeps handling a guest without one (guest view `payment: null`, `409 NO_BALANCE_PAYMENT` on paying it, the console's guests grid "no payment").

## Data (migration 0015)

- `trips.balance_due_days integer not null default 30 check (balance_due_days between 0 and 120)` (D20). Existing trips get 30.
- `trips.balance_requested_at` dropped.
- `trips.days` check replaced: `days between 1 and 21`; `cancellations.trip_day` check replaced: `trip_day between 1 and 21` (D20 input bounds; the constraint names are PostgreSQL's defaults, `trips_days_check` and `cancellations_trip_day_check`).
- `delete from due_actions where kind = 'request_balance'`, then the `due_actions_kind_check` without `request_balance`. Done and failed `request_balance` rows go too: the kind no longer exists, and the console's failed-actions list would show work nothing will redo.
- A balance opened before 0015, still `pending`, `authorize` and with no money command, becomes `capture`: the guest page's checkout now loads PayPal for CAPTURE only, so its AUTHORIZE order would fail. One with a command keeps its intent, so reconciliation reads the order it placed. This changes an intent, it creates no payment (review finding 9).
- Migrations 0001–0014 are not edited.

## Trips (`trips/store.py`, `trips/http.py`)

- `MAX_TRIP_DAYS = 21` (D20 input bound, no longer a PayPal limit); `NewTrip.balance_due_days: int = 30`, request field `Field(default=30, strict=True, ge=0, le=120)`; inserted with the trip.
- `invite` creates the deposit and the balance payment in the same transaction, both `intent = 'capture'`; `open_balances`, `_open_balances` and the `balance_requested_at` read go. A trip cancelled as a whole still takes no new guest (unchanged), so no payment is created for it.
- The operator's `Trip` view drops `balance_requested_at` and gains `balance_due_days` (see "Schedule" for why not the instant).

## Schedule (`holds/schedule.py`)

- `balance_due_at(trip, invited_at) -> datetime`: local midnight (trip time zone) of `starts_on − balance_due_days`, in UTC; when that is before `invited_at` (the guest's `created_at`), `invited_at`. ADR D1 says "at once for later bookings", and a booking is a guest's: keyed to the trip's creation, a guest invited after the due date would see a due date before its own invitation (review finding 2). For a guest invited when the trip is created it equals the plan's "at once when the trip is booked later". Read by the guest view now and by 8b's unpaid-balance ask (`check_trip`, which sits above `holds`).
- `invited_at` is the database clock (`guests.created_at default now()`), not a due action's injected `now`; the docstring says so, and 8b's ask tests set the invitation time they need (review finding 4).
- Because `trips` is below `holds`, the operator's `Trip` view in `trips.http` cannot call it. The operator view shows `balance_due_days` only; the due date is shown on the guest page (and in 8b/9 from settlement, which can import `holds`).
- `AUTHORIZATION_VALID_DAYS`, `balance_opens_at`, `ensure_schedules` and `request_balance` go; the module docstring is rewritten. `holds/__init__.py`'s docstring ("built in a later plan step") is rewritten too: the module serves the guest's link and payments and the balance due date; it keeps its name (plan step 6).
- `trips.store.GUEST_PAYMENTS` also selects the guest's `created_at`.

## Due actions and worker

- `platform/due_actions.py`: `request_balance` leaves `Kind`, `LEASES` and `SUBJECT`; `retry`'s docstring no longer names it.
- `bootstrap/worker.py`: no `request_balance` handler, `tick` no longer calls `holds.schedule.ensure_schedules`.
- `settlement/http.py`: the failed-actions docstring drops the `request_balance` note, and a cancellation's `trip_day` is bounded by `trips.MAX_TRIP_DAYS` instead of a literal 10 (found by the 21-day test).

## Guest routes (`holds/http.py`)

- `Balance` gets `due_on: date` (the local date of the guest's `balance_due_at` in the trip's time zone) in place of `opens_at`; `payment` is `None` only for a guest invited before migration 0015.
- Paying a balance that has no payment answers `409 NO_BALANCE_PAYMENT` (was `BALANCE_NOT_OPEN_YET`).
- The checkout and test-card routes are unchanged: `money.service` already opens a CAPTURE order from `payments.intent` (step 4).

## Web

- `PayPalCheckout.tsx`: one SDK load with `intent=capture` for both purposes (the per-intent map and namespaces go); the cancel message no longer says "or held".
- `guest/types.ts`: `balance: { due_on, amount_minor, payment }`. `GuestTrip.tsx`: the balance line reads "USD 300.00, due 2026-11-04" while unpaid (or without a payment), and "USD 300.00: paid" once captured; the `authorized` and `voided` labels stay until step 9 (8a's holds still exist). `GuestPayments.tsx`: the balance heading is "Pay the balance" (the due date is on the balance line); the other wording stays step 9's. `next-payment.ts`: docstring only.
- `trips/types.ts`: `balance_requested_at` replaced by `balance_due_days`. `TripForm.tsx`: `max={21}`; the `balance_due_days` input is step 9's (the API defaults it to 30). `GuestsSection.tsx`: a guest without a balance payment reads "no payment" (was "not open").
- `describe.test.ts` uses the new 409 code.
- `web/e2e/demo-path.spec.ts`: the guest pays the deposit, then the balance at once (no waiting for a worker tick), and sees "Balance: USD 300.00: paid"; the decision panel and the guest outcome assert what 8a shows for a captured balance (no balance release; 8b and 9 change these).

## Docs

- `docs/threat-model.md`: the validity row's proof column says the 10-day limit and its test went in step 6 (the row itself retires in 8c).
- `README.md`: unchanged here (its hold wording retires in 8c and 12).

## Tests (plan's names)

- Removed (commit's `Quality-lock-reason:`): `test_the_last_trip_day_falls_on_day_28_of_the_hold` (parametrized 1–10: 10 executed tests), `test_a_sixty_day_out_trip_is_not_held_at_booking` (both tests of `tests/unit/test_schedule.py`, which then holds the new unit tests), `test_the_balance_hold_opens_at_t_minus_29_minus_n`, `test_a_trip_booked_inside_the_window_opens_at_the_next_tick`, `test_a_guest_invited_after_the_balance_opened_gets_a_balance_payment`, `test_a_trip_longer_than_ten_days_is_refused`: 15 executed tests.
- Changed: `test_each_kind_is_claimed_under_its_own_lease` (`check_trip` instead of `request_balance`), `test_judge_flow_and_workspace_isolation` (balance paid at once, `captured`), `test_the_trip_timeline_lists_each_guests_money_events` (the balance's `payment_created` at invitation), `test_without_paypal_credentials_payments_are_unavailable` (the balance answers 501 too), `payloads.py` (`TRIP`'s comment), `GuestTrip.test.tsx`, `describe.test.ts`; not in the plan's list, broken by this change (review finding 1): `test_a_guest_link_shows_the_guests_trip` (a pending balance payment), `test_a_trip_cancelled_as_a_whole_takes_no_new_guest_hold_or_payment` (8b's list; the balance payment is pending, the late invitation and payment still refused); `test_settlement.py`'s holds as decided above; `test_due_actions.py`'s generic payment is a capture balance.
- New:
  - `tests/unit/test_schedule.py`: `test_the_balance_falls_due_its_days_before_the_first_local_trip_day` (exact UTC instants for 3-day trips in `Asia/Manila`, `America/Los_Angeles`, and Los Angeles on 2026-11-01, the day US daylight saving time ends; review finding 7), `test_a_trip_booked_after_its_due_date_is_due_at_once` (a guest invited 10 days before a 30-day due date), `test_a_zero_day_balance_falls_due_when_the_first_local_trip_day_begins`.
  - `tests/integration/test_holds.py`: `test_a_guest_gets_its_balance_payment_at_invitation` (pending, `intent = 'capture'`, guest view `due_on`, for a trip starting 60 days from today so the test does not age; review finding 3), `test_the_balance_is_captured_when_the_guest_pays` (test card and checkout: `captured`, every PayPal order `CAPTURE`), `test_balance_due_days_outside_0_to_120_are_refused` (−1, 121, "30", 30.0 → 400), `test_balance_due_days_at_their_bounds_are_accepted` (0, 120).
  - `tests/integration/test_thin_path.py`: `test_a_trip_of_the_longest_allowed_length_is_accepted_and_one_longer_is_refused` (21 → 201 and its day 21 can be cancelled, 22 → 400).
- Counts (`quality-lock.json`): backend-unit 98 → 92 (−11, +5), backend-integration 172 → 177 (−4, +9 with parametrized cases), web 20 → 21, e2e 12.
- Mutation: compute the due date from the last trip day → `test_the_balance_falls_due_its_days_before_the_first_local_trip_day` fails.
- Acceptance outside the tests: `make coverage` read and recorded in `docs/evidence.md`; the browser check of the sandbox checkouts is the developer's (plan "Needs a human"), with its steps in `docs/evidence.md`; the plan's Progress row for step 6 (review finding 5).

## Design review at start (2026-10-23)

An independent reviewer (a subagent that wrote none of this) read this design against the plan, the ADR, `AGENTS.md` and the code. 9 findings: 8 changed the design or the code, 1 accepted with a note.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | Tests broken and not listed: `test_a_guest_link_shows_the_guests_trip`, `test_a_trip_cancelled_as_a_whole_takes_no_new_guest_hold_or_payment`, three direct balance payments in `test_settlement.py`, not two | HIGH | Changed: listed and updated (Tests, Decided) |
| 2 | "At once for later bookings" keyed to the trip, so a guest invited after the due date sees a date before its invitation | HIGH | Changed: `balance_due_at(trip, invited_at)` per guest, ADR D1's wording; reported to the developer as a reading of the plan's "when the trip is booked later" |
| 3 | The integration test's fixed trip date stops working on 2026-11-05 | MED | Changed: the trip starts 60 days from today |
| 4 | The invitation time is the database clock, due actions use an injected `now` | MED | Accepted and documented in `holds/schedule.py`; 8b's ask tests set the invitation time |
| 5 | The browser check, `make coverage` and the Progress row missing from the design | MED | Changed: listed under Tests |
| 6 | Quality-lock counts and the removed executions not stated; "`test_schedule.py` goes" contradicted | MED | Changed: counts and wording above |
| 7 | "One second before" assertion holds for any value | LOW | Changed: exact instants, plus the DST day |
| 8 | e2e and console wording not pinned; `GuestsSection.tsx` "not open" | LOW | Changed: the strings are named; "no payment" |
| 9 | Pending `authorize` balances in local databases fail the CAPTURE-only checkout | LOW | Changed: 0015 sets such a balance without commands to `capture` (an intent change, not the backfill the developer declined) |
