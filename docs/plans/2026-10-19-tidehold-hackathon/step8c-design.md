# Step 8c design: retire the authorization paths

Premise: the code changed here is the hold (PayPal AUTHORIZE) path that D18 retired; steps 6 and 8b no longer call it (`rg -n "record_command|intent=\"authorize\"|authorize_order|\.void\(|reauthorize" backend/src web/src` at e233c93 finds only `money/`, `bootstrap/sandbox.py` and `settlement/plan.py`). No deployment holds tidehold data: step 10 has not run, `make dev-db` is `docker run --rm` without a volume, CI and the integration suite start from an empty database.

The plan's step 8c Scope is the work list. This file records only where the build differs from it, and why.

## Where the build differs from the plan text

1. **`settlement/plan.py` is deleted, not rewritten as refund-or-nothing.** Step 8b moved every settlement amount into `settlement/evaluate.py` (`_amount`, netted against what earlier accepted proposals returned) and every move into `settlement/moves.py` (`move`: refund, waive, credit, wait); nothing imports `plan.py` (`rg -n "settlement.plan|settle_payment" backend/src` finds only the file itself). A refund-or-nothing `settle_payment` would be a second, unused statement of the same rule. `test_settlement_conserves_money_for_every_small_case` keeps its name and moves to `tests/unit/test_settlement_evaluate.py`, asserting the per-guest invariant on the code that runs: over every small base, percent, deduction and sequence of day cancellations, the lines of the accepted proposals for one guest and payment never exceed the guest's base and are never negative. `test_a_payment_not_yet_placed_cannot_be_settled` goes with `plan.py` (a pending payment is waived since 8b, `test_settlement.py` covers it).
2. **Migration 0017 refuses hold-era rows instead of converting them.** Necessity condition for a conversion: some database we keep has `authorized` or `voided` payments, `authorize` intents, or `capture`/`void`/`reauthorize` commands. It does not hold (premise above). The migration checks first and raises `HOLD_ROWS_PRESENT: recreate the database` if any exist, then tightens the constraints; a conversion would be untested code for data that does not exist.
3. **The acceptance `rg` over `backend/src` also allows PayPal's order status `VOIDED`** (`money/service.py` `_not_placed`). It is PayPal's status for an order that will never be paid, not our void command; reading such an order as "not placed" lets the guest start a new attempt, and dropping it would turn that read into `undecided`, a behaviour change outside this step.
4. **The acceptance `rg` over `web/src` allows "released" where it means the D21 release of a guest from its unpaid balance** (`guest/types.ts` `released`, `next-payment.ts`, `GuestTrip.tsx` "released by the operator, nothing more to pay"). The plan's acceptance was written 2026-10-23, before D21 named the operator's answer `release`; the hold meaning ("held (authorized, not charged)", state `voided` shown as "released") is removed.

## Tests beyond the plan's lists

- Removed: `test_a_refused_resend_of_a_hold_capture_is_marked_and_not_sent_again` (its kind is gone; the same marking for a refund is `test_a_refused_resend_keeps_the_command_open_and_its_key`), `test_a_payment_not_yet_placed_cannot_be_settled` (item 1).
- Renamed: `test_the_smoke_runs_the_three_money_flows` → `test_the_smoke_runs_the_money_flows` (two flows remain; the name was already wrong at four).
- The fake's `card_status` knob now sets the status of a card capture (it set the hold's status), so `test_an_unexpected_paypal_answer_keeps_the_attempt_open` keeps testing an answer we cannot record, now `PAYPAL_CAPTURE_UNEXPECTED`.
- `tests/unit/test_paypal.py`: the three tests that called `client.void` call `client.refund`.

## Schema after 0017

- `payments`: `state in ('pending', 'captured')`; `intent`, `authorization_id`, `authorization_expires_at` dropped with their checks (`payments_intent_purpose`, `payments_authorized_intent`, `state <> 'authorized' or authorization_id is not null`, `purpose = 'balance' or state in ('pending','captured')`).
- `money_commands`: `kind in ('open', 'refund')`; `money_commands_once` becomes one live open attempt per payment (`where kind = 'open' and status <> 'rejected'`); `money_commands_one_reauthorization_in_flight` dropped. The refund bound and one live refund per proposal and payment (0012) are unchanged.

## New test

`test_the_command_kinds_are_open_and_refund`: `typing.get_args(commands.Kind) == ("open", "refund")`, and inserting a `void` command row is refused by the database check. Mutation: add `void` back to `Kind` → the test fails.

## Review at start (2026-10-24)

An independent design reviewer read this file and the plan's step 8c against e233c93: 8 findings, 7 changed, 1 rejected.

- Changed: the acceptance `rg` over `backend/src` excludes `platform/migrations/` (0001–0016 must not change, and 0017 has to name what it drops) and allows by file the `Authorization` header (`money/paypal.py`, `policy/providers.py`), `HTTP_401_UNAUTHORIZED` (`identity/sessions.py`) and PayPal's order status `VOIDED` (`money/service.py`); `settlement/execute.py`'s docstring says "decided it" instead of "authorized it". The search also covers `PAYMENT_HELD|placed hold`.
- Changed: the three refusals of a placed hold 8b added are removed: `commands.waive` (`PAYMENT_HELD`), `moves.move` (`case _`; it now takes a `Literal["pending", "captured"]` state and mypy checks the match is exhaustive) and `execute._can_move` (its `except Conflict`).
- Changed: the one-kind and one-intent shells go too: `record_command`, `NEEDS_STATE` and `TARGET_OF` (a refund reads `capture_id` itself), `_placed_by`'s `capture` flag, `create_order`'s `intent` parameter (always `CAPTURE`), `create_payment`'s `intent`, `commands.Intent`, and the smoke's `(purpose, intent, steps)` table.
- Changed: 0017 tightens `money_commands.amount_minor` to `> 0`; its only zero-amount kind was `void`.
- Changed: `AGENTS.md` (Reuse: `settlement/plan.py` deleted in 8c, its conservation test runs on `evaluate`), `settlement/__init__.py`, `docs/threat-model.md` lines on holds (header, surface 2 rows naming a hold, the retired validity row) change in the same commit.
- Changed: the web search is an allow-list by path: `rg -n -i "authoriz|reauthor|honor|held|voided" web/src web/e2e` finds nothing, and `released` appears only in `guest/types.ts`, `guest/next-payment.ts`, `guest/GuestTrip.tsx` and `GuestTrip.test.tsx` as the D21 release. 8c removes the hold states from `guest/types.ts` and their labels from `GuestTrip.tsx` (they mirror the backend states this step drops); the rest of the guest page stays step 9's.
- Recorded: `money_commands.target_id` stays as the snapshot of the capture a refund was sent against (it is what `_refund` sends and what the threat model's audit reads); since 8c it always equals `payments.capture_id`, and the check `(kind = 'open') = (target_id is null)` still holds.
- Rejected: renaming `test_settlement_conserves_money_for_every_small_case`. The name states the property, and the moved test still asserts it, now on the code that computes the amounts; the provenance change goes in the commit message.

## Code review before commit (2026-10-24)

An independent reviewer applied 0001–0017 to a scratch PostgreSQL 18 and compared `pg_constraint`: 0017 dropped `payments_check`, which is 0003's refund bound (`refunded_minor between 0 and captured_minor`; PostgreSQL names a column check that reads two columns as a table check), not "only a balance is ever held" (`payments_check1`). Fixed to drop `payments_check1`; `test_the_database_refuses_refunds_above_the_capture` fails on the first version and passes on the fix. No other finding.
