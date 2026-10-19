# Evidence

Measurements, coverage reports and sandbox runs that plan steps cite in their acceptance. Newest first within each step.

## Step 4: money engine port

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
