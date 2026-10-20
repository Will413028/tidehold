# Step 8a design: cancellation, matching, asks and the deposit

Plan step 8a (`../2026-10-19-tidehold-hackathon.md`), decisions D16 (ADR `docs/adr/2026-10-19-settlement-triggers-and-model-role.md`) and D17. Written 2026-10-20 at c638367, before building; revised after the start-of-change design review (10 findings: 7 changed, 1 recorded, 2 rejected; listed at the end).

## Flow

1. The operator records a cancellation: a trip day or the rest of the trip, with its cause. The request evaluates the bound policy against the evidence visible at that moment (no model call) and stores one settlement proposal for it.
2. A proposal that leaves nothing to choose and fits the trip's mandate is accepted by the mandate. Any other proposal is an ask: the operator picks an option or rejects. Rejecting withdraws the cancellation (`cancellations.withdrawn_at`; a whole trip reopens): no money moves, and the operator can record it again.
3. An accepted proposal schedules `settle_proposal`. On a whole-trip cancellation it moves money now: deposit refunds and balance voids. A balance that still owes a capture, and the money of a day cancellation, wait for finalize. Step 8b captures at once on a whole-trip cancellation. Deposit credit is a ledger row written at acceptance. A balance credit is written by 8b when its capture succeeds.
4. A periodic `check_trip` action opens three kinds of ask:
   - a warning in the bound event set, in force on a trip day with no cancellation;
   - a warning whose value is outside the catalogue;
   - from the first trip day on, a guest with no authorized balance.
5. Every ask stores its template question first. A `draft_ask` action then asks the model for better wording under its own daily cap. When the model fails or the cap is reached, the template stays.

## Data (migration 0010)

### `trips`

- `timezone`: an IANA name, required at creation and checked with `zoneinfo`. Trip days are local dates; a PAGASA trip is UTC+8.
- `mandate_guest_minor` and `mandate_trip_minor` (≥0, required): the most the trip's accepted proposals together may return without the operator. Returned money is refund + release + credit; the guest cap applies per guest.
- `cancelled_at`: set, under the trip row's lock, when the operator cancels the whole trip. After it no guest is invited, no balance opens and no guest payment starts (`TRIP_CANCELLED`), and no `check_trip` is derived.

### `cancellations`

- Columns: `trip_id`, `scope` (`day` | `rest`), `trip_day` (1..days; for `rest`, the first day it actually covers), `cause` (`weather` | `safety` | `other`), `recorded_at`. A `rest` request from day 1 is the whole trip, even when some days were already cancelled.
- A day is cancelled at most once. A `rest` cancellation covers its first day through the last one not already cancelled. After a `rest` cancellation, nothing else can be cancelled.
- `recorded_at` is the decision time.

### `proposals`

- Identity and links: `trip_id`, `kind` (`settlement` | `warning` | `unauthorized_guest`), `subject` (text), `cancellation_id` (settlement only).
- State: `state` runs `asked` → `accepted` → `executed`, or ends at `rejected`, `dismissed` or `answered`.
- Decision: `reasons` (text[], why it asks), `parts` (jsonb: per purpose, the options, each an outcome per guest computed by code), `choice` (jsonb: the chosen option index per purpose), `decided_by` (`mandate` | `operator`).
- Text: `question` (the template), `draft` and `draft_call_id`.
- `facts` (jsonb): the warning or payment fields the ask shows, as visible at its decision time.
- Times: `created_at`, `decided_at`.
- Unique on (trip_id, kind, subject). The subjects are:
  - `cancellation:<id>`;
  - `day:<n>:<event value>`, so one ask per trip day and event value: an Update or a replay of the same warning does not ask again;
  - `guest:<id>`.

### Other tables

- `credits`: `guest_id`, `proposal_id`, `purpose`, `amount_minor`, `expires_at` (from `credit_expiry_months`; null when the rule states none). Unique on (proposal_id, guest_id, purpose); append-only, enforced by a trigger.
- `money_commands.proposal_id`: nullable, null for `open`. 8b's finalize capture is authorized by its own finalize proposal, so one link per command is enough.
- `due_actions`:
  - new kinds: `check_trip` (subject: trip), `settle_proposal` and `draft_ask` (subject: proposal, through a new `proposal_id uuid` column);
  - the subject check becomes "exactly one of trip, payment, proposal", and the open-unique index covers all three.
- `model_calls.purpose` gains `draft_ask`. Drafts have their own daily cap (setting `model_daily_draft_cap`, default 20) and also count toward the live cap, so judges' compiles keep the rest of the quota.

## Evaluation (`settlement/evaluate.py`, pure)

**Inputs.** The rules of the trip's product with their spans and the rulings' spans; the cancellation; the trip (days, timezone, mandate); each guest's deposit and balance payment; what the trip's earlier accepted proposals returned per guest and purpose; and the matched warnings per rule.

**Which rules cover a cancellation.** A rule covers a cancellation when its causes include the cancellation's cause or `any`, and its scope fits:

- A day cancellation fits `per_day`.
- A `rest` cancellation fits `per_trip`. If no per-trip rule exists for a purpose, `per_day` rules apply to each covered day.

For each purpose (deposit, balance), the candidates are the covering rules whose `applies_to` names it.

**Warning conditions.** A rule with a warning condition counts only when a warning matches (D16 ADR, matching). A warning matches when all of these hold:

- it comes from the trip's source and areas;
- its event value is in the rule's set;
- it is `Actual`, with no visible replacement;
- it was visible at `recorded_at`;
- it was in force during a covered local trip day.

When no warning matches, the reason is `WARNING_NOT_MATCHED`, with the options "apply the rule" or "no remedy".

**Reasons that always ask:**

- `NO_RULE`: no candidate. Options: none, refund 100%, or credit 100% with no expiry. These are standard outcomes computed by code; the model supplies no amount.
- `CONFLICTING_RULES`: the candidates disagree. Each candidate's remedies become an option.
- `REMEDY_CHOICE`: a rule has two or more remedies. Each remedy becomes an option. When the guest chooses, the operator records the guest's choice; there is no guest product.
- `RULED_IN_TEXT`: a ruling's span overlaps the rule's span or its warning span. The operator's free-text answer may change the outcome, and code cannot read it. The invariant "an unruled or discretionary clause never auto-executes" (R10) therefore covers clauses ruled in text. Trigger to structure rulings into rule edits at publish: a real operator's policy makes most proposals ask for this reason.
- `FIXED_DEDUCTION`: a remedy has a fixed deduction. The rule schema has no currency, and the wording may name PHP or EUR. A percent deduction is computed.

**Amounts.** Per guest and purpose, in minor units, computed exactly: percentages are read as `Fraction(str(value))` and rounded down once, at the end.

- base = the deposit's net captured amount, or the authorized balance (0 when that payment is not placed).
- share = base × covered days ÷ trip days for a per-day rule; base for a per-trip rule.
- portion = share × percent ÷ 100 × (1 − the sum of percent deductions ÷ 100).
- The portion is netted against earlier proposals: it is at most base − what the trip's accepted proposals already returned for that payment.
- Remedy `refund`: the deposit returns the portion, and the balance releases it. Remedy `credit`: a credit of the portion (the balance portion is still captured). Remedy `none`: nothing.

**Mandate.** The returned money of this option plus the trip's accepted and executed proposals is checked per guest and per trip against the trip's caps. Exceeding a cap gives `MANDATE_GUEST_CAP` or `MANDATE_TRIP_CAP`. Auto-accept needs no reasons, one option per purpose, and both caps met.

Re-evaluated from dive-trip-agent `check_mandate`:

- `allowedCauses` is dropped: rules carry causes.
- `allowedActions` is dropped: every kind (refund, release, credit) counts against the same caps, and an operator who wants nothing automatic sets the caps to 0.
- `expiresOn` is dropped: the trip bounds the mandate, and a cancellation after finalize asks (D16 ADR, 8b).
- The caps stay, summed across the trip's proposals as the source's per-booking cap was (one settlement per booking there).

## Money (`settlement/execute.py`, the `settle_proposal` handler)

The handler re-reads the proposal and the trip and acts only on `accepted`. On a `rest` cancellation that covers the trip's first day (the whole trip), for each guest:

- a deposit refund > 0 records a `refund` command with the proposal and executes it;
- a balance whose owed amount (authorized − released by the trip's accepted proposals) is 0 records a `void` (`settlement.plan.settle_payment`) and executes it.

A guest's payment that was not placed when the settlement was evaluated (the line's `base_minor` is 0, or the guest has no line) completed while the trip was being cancelled:

- such a balance hold is voided, because the trip will not happen and no outcome claims it;
- such a deposit becomes a `late_payment` ask.

Such a payment can also land after the settlement ran (an attempt in flight, or one reconciled later). `execute.ensure_schedules` checks, on every tick for 30 days after a whole-trip cancellation, for a late hold with no void sent or a late deposit not yet asked about, and runs the settlement again; a rerun finds the commands it already sent and sends only what is new.

Other cases:

- An existing command for (payment, kind, proposal) is reused, so a rerun resumes.
- A `rejected` outcome ends the line and opens an ask; 8b adds the new-checkout recovery.
- An `unknown` outcome is left to `reconcile_payment`, and the handler raises `RetryLater`.
- When every line is settled, the proposal becomes `executed`.
- No PayPal client also raises `RetryLater`.
- A command that stays open after a refused resend makes the action fail after its retries; 8b turns it into an ask (plan step 8b scope).

## Periodic check (`check_trip`)

`ensure_schedules` derives one `check_trip` per trip not cancelled as a whole, from 2 days before its start through the end of its last local day. It runs only when no row is open. The handler locks the trip row like a cancellation does, so it never opens an ask on a day cancelled meanwhile. `due_at` is the later of the window start and the last finish plus 10 minutes. The handler:

- **Warnings:** reads the evidence visible now, for the trip's source and areas, in force on a local trip day that has no cancellation.
  - A value in the union of the product's rule event sets gives a `warning` ask.
  - A value outside the catalogue gives a `warning` ask with `UNKNOWN_EVENT`.
- **Unauthorized guests:** from the first local trip day on, a guest whose balance is not `authorized` or `captured` gets an `unauthorized_guest` ask.
- **Drafting:** each new ask schedules `draft_ask` in the same transaction.

Recording a cancellation answers the open warning asks of the days it covers. The operator can dismiss an ask.

## Drafting (`settlement/drafts.py`, the `draft_ask` handler)

- **Input:** the stored `facts` and `question` only.
- **Output:** `{"text": string}`, up to 600 characters, through `Provider.complete` with the drafting prompt's own hash.
- **Calls:** `policy.calls` keeps the cap, replay and audit for every purpose; `policy.service._run` is generalised to take a prompt hash and a per-purpose cap.
- **Lease:** 120 s, against a provider timeout of 90 s. `settle_proposal` gets 600 s: up to two PayPal calls of at most 20 s per guest, 12 guests a trip.
- **Failure:** any model failure, or a `no_ai` outcome, completes the action and keeps the template.
- **What the operator sees:** the ask's API returns `facts` and `question` beside `draft` (threat model surface 4).

## HTTP (workspace access class, `settlement/http.py`)

- `POST /api/trips/{trip_id}/cancellations` with `{scope, trip_day, cause}` returns the settlement proposal.
- `GET /api/trips/{trip_id}/proposals`.
- `POST /api/trips/{trip_id}/proposals/{proposal_id}/decision` with `{"accept": {"deposit": i, "balance": j}}`, `{"reject": true}` or `{"dismiss": true}`.
- Request models are strict, and an error never echoes the input.

## Out of 8a

- Owned by 8b:
  - finalize;
  - captures (including the whole-trip capture at once);
  - reauthorization;
  - the money of day cancellations;
  - balance credits;
  - refused-capture recovery;
  - asks for commands left open after a refused resend.
- Guest-requested cancellations: the rule schema's causes have no such cause. Trigger: a labelled clause that treats them differently.
- Console screens and the replay trigger for an immediate check (step 9).

## Design review at start (2026-10-20)

| Finding | Disposition |
|---|---|
| Trip cap applied per proposal, so day cancellations add up past it | Changed: caps sum the trip's accepted proposals |
| Free-text rulings ignored by evaluation | Changed: `RULED_IN_TEXT` always asks; trigger to structure rulings recorded |
| Fixed deduction assumed USD; float percentages | Changed: `FIXED_DEDUCTION` asks; exact fractions |
| Warning subject by CAP identifier re-asks on every Update | Changed: subject by day and event value |
| `facts` snapshot duplicates stored rows | Rejected: a replay's shift is rewritten on re-replay (`warnings.store.replay` upserts it), so the ask must keep what was visible at its decision time |
| Whole-trip balance capture waits; credit before capture | Changed: 8b captures at once on a whole-trip cancellation; balance credit is written on capture |
| Whole trip refused after a day cancellation | Changed: `rest` cancellation, netted against earlier proposals |
| UTC trip days | Changed: trip timezone |
| Open-after-refused-resend leaves the proposal stuck | Recorded: 8b scope, listed above |
| One `proposal_id` per command cannot cover a finalize capture | Rejected: 8b's finalize is its own proposal |
| Drafts share the compile cap | Changed: own daily draft cap |

## Code review before commit (2026-10-20)

| Finding | Disposition |
|---|---|
| A whole-trip cancellation did not stop new guests, the balance opening or guest payments; a hold placed after evaluation was never voided (HIGH) | Fixed: `trips.cancelled_at`; late holds voided, late deposits asked about |
| A guest invited after evaluation crashed `settle_proposal` (HIGH) | Fixed: no line means placed late |
| The evidence-time assertion checked nothing (MED) | Fixed: a matched warning's `visible_at` precedes `recorded_at` |
| `rest` from an already-cancelled day hit the unique key (500) (MED) | Fixed: stored from the first day it covers |
| `AGENTS.md` edited without assignment (HIGH by rule) | Kept and reported: the contract line now says "a trip day, or the rest of the trip from a day", the semantics this design adopted from the start-of-change review |
| `settle_proposal` could outlive its 120 s lease (LOW) | Fixed: 600 s |
| A check racing a cancellation could leave a stale ask (LOW) | Fixed: the check locks the trip row |
| Round 2: a payment that lands after the whole-trip settlement ran stays held, unasked (HIGH) | Fixed: the late-payment sweep above |
| Round 2: rejecting a whole-trip settlement left the trip cancelled with nothing settled (MED) | Fixed: rejecting withdraws the cancellation |
| Round 3: the trip check still counted a withdrawn cancellation; warning asks it answered stayed closed (MED ×2) | Fixed: the check reads only cancellations not withdrawn, and withdrawing reopens the warning asks of its days (no round 4: each fix has a test whose mutation fails) |
