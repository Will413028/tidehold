# Step 9 design: operator console and guest page (the part 8a backs)

Plan step 9 (`../2026-10-19-tidehold-hackathon.md`), requirements R12 and R13 (`requirements.md`), decisions D10 (AG Grid), D12 (replay), D15 (do-not-enter-real-data notice) and D16. Written 2026-10-20 at fcb7225, before building; revised after the start-of-change design review (9 findings: 7 changed, 1 recorded, 1 decided by the developer; listed at the end). Step 9 starts before 8b by the developer's choice of 2026-10-20: 8b waits for step 4's day-4 reauthorization check (not before 2026-10-23 12:25 UTC). Everything here reads data 8a already stores; the finalize, capture and reauthorization screens wait for 8b (listed under "Waits for 8b"; D18 removed them, and that section became "D18 screens" on 2026-10-24).

Dependencies approved by the developer on 2026-10-20: `ag-grid-community` and `ag-grid-react` 36.2.0 (released 2026-09-16), `@playwright/test` 1.64.0 (2026-10-07; 1.64.1 is inside the one-week rule).

## Where things stand

- The web console creates a trip and invites guests, but its trip form no longer matches the API: `NewTrip` has required `policy_version_id`, `timezone`, `mandate_guest_minor` and `mandate_trip_minor` since steps 5 and 8a, so creating a trip from the console fails today.
- The guest page shows the deposit and the balance hold and takes payments; it shows no cancellation, clause or outcome.
- The backend has every operator write the console needs (compile, event-set proposal, publish, trip, guest, cancellation, decision) and the reads for trips, timeline, versions and proposals. It has no route for the evidence a trip sees, for replaying a recorded warning (D12), for failed due actions, or for a guest's settlement.

## Backend additions

Each item names the condition that makes it necessary.

1. **Option provenance.** Each option in a proposal's `parts` gains `rules`: the indexes into the proposal's own `facts.clauses` of every rule that gave this remedy (options are deduplicated by remedy, `evaluate._options`, so one option can come from several rules); empty for the standard `NO_RULE` options and "no remedy". The clause shown is `facts.clauses[i].quote`, the snapshot taken at the decision, never re-cut from the version (the same snapshot rule as 8a's `facts`). Necessary because R12 shows "the clause" behind a guest's outcome, and today an option carries only its label. Stored proposals without the key show no clause (the demo database is rebuilt nightly; jsonb, no migration).
2. **`GET /api/trips/{trip_id}/evidence`** (settlement module, workspace class): the warnings visible now for the trip's source and areas, in force on any local trip day (`matching.in_force`), each with `recorded_at`, `visible_at`, `replayed` and the trip days it covers; plus the Open-Meteo daily wave height for the trip dates, labelled "forecast, corroborates only", with its fetch time. Necessary because R13 lists evidence and the operator decides a cancellation from it; an ask shows only one warning as it looked at one decision.
   - Open-Meteo: `openmeteo.fetch` gains a `timezone` argument and is called with the trip's (trip days are local, 8a), keeping its 15 s timeout. Each fetch is written with `openmeteo.store` (the `corroborations` table, which has no writer today), and the route reuses the trip site's rows fetched within the last hour. Necessary because a page reload must not call the API each time and the fetch time is what the label shows. `Unavailable` shows as "forecast unavailable", never an error; dates the API does not answer show as "no forecast yet".
   - The HTTP client is one `httpx.AsyncClient` on `app.state`, closed in the lifespan like the PayPal client.
   - Settlement sits above `warnings` and `trips`, so no layer moves.
3. **`GET /api/recorded-warnings?source=&area=`** and **`POST /api/trips/{trip_id}/replays`** with `{warning_id}` (settlement module, workspace class). The list returns recorded warnings of one source, optionally one area, newest first, at most 50. The replay writes the warning into the workspace as fetched now (`warnings.store.replay`) and then runs the trip check in the same transaction: `watch.check_trip`'s body becomes `check(conn, trip_id, now)`, called by both the due action and this route, so the ask exists when the response returns. Necessary because the demo path needs a warning in force on a trip day, and every recorded warning has expired by the time a judge runs the demo (D12). Refused for a trip cancelled as a whole (`TRIP_CANCELLED`). No replay cap: `warning_replays` is unique on (workspace, warning) and upserts, so a workspace holds at most one row per recorded warning; the asks a replay opens are drafted under the existing draft cap, after the response (`draft_ask`).
   - **Premise (recorded):** a replay aligns the fetch time to now, so the replayed warning is in force around now. It matches only a trip whose local trip day is today, so the e2e and the demo seed create trips that start today in their timezone. Aligning to a future trip day instead is not built: the warning's `visible_at` would lie in the future, and a decision sees only evidence visible at its time (plan invariant), so a cancellation recorded now would not see it.
4. **`GET /api/trips/{trip_id}/failed-actions`** (settlement module, workspace class): the trip's failed due actions, one row per (kind, subject) with the latest `last_error`, `finished_at` and the count of failures. Trip-kind actions match on `trip_id`; payment-kind ones join `payments` to the trip's guests; proposal-kind ones join `proposals.trip_id`. `last_error` is an exception class name or a `RetryLater` code (`run_due` writes only those). Necessary because nothing else shows a failed action: `request_balance` is never recreated after a failure (`holds.schedule.ensure_schedules`), and `check_trip` and `settle_proposal` are recreated after one, so without the grouping a failing check adds a row about every 90 minutes. The `retry` docstring's "not recreated" is corrected to say which kinds.
5. **`GET /api/guest/{token}/settlement`** (settlement module, guest class, a second router registered under `Access.GUEST`): for each settlement proposal of the guest's trip that is not rejected, the cancellation (days, cause, recorded time), its state (`asked`: the operator is deciding; `accepted`; `executed`), the matched warnings from `facts.clauses` with source, event, fetch time and the "recorded, replayed" label, and for an accepted or executed proposal this guest's lines of the chosen option per purpose with the clauses of the option's `rules`; plus the guest's credits (amount, purpose, expiry). Each proposal carries one `explanation` sentence built by the backend ("The operator cancelled day 2 for weather. Gale Warning (NWS) was in force."); the backend is its only producer and the web only shows it, so step 11's model draft replaces the same field. The sentence carries no amount: the web shows each outcome from its stored line, so a drafted sentence can never state one (milestone review, below). USD amounts in backend text go through one helper (also used by `settlement/execute.py`'s two hand-written formats). No other guest's name or amount, and no workspace, payment or PayPal ids (the `GuestView` rule). Necessary because R12 needs these, and `holds` (where today's guest view lives) sits below `settlement` and cannot read proposals.
   - A guest explanation drafted by the model (the product contract) waits for step 11: the template is the fallback the contract requires, and drafting needs its own purpose, cap and threat-model line. Trigger: step 11 starts.

`docs/threat-model.md` gains two lines in the commits that add the surfaces: the replay route (any workspace session can make a recorded warning in force for its own trips; contained by sandbox only, the workspace scope of `warning_replays` and the "recorded, replayed" label; trigger: any non-sandbox credential puts the route behind a demo setting), and the guest settlement route (token-only access; proved by a test that a guest sees no other guest's name or amount).

## Web

Routes (`app` imports a feature only through its `index.ts`):

| Route | Feature | Shows |
|---|---|---|
| `/` | `trips` | the trips grid and the new-trip form (policy version and product, timezone, mandate caps added) |
| `/policies` | `policy` | the wording box; compile; rules with their clause; needs-ruling items to answer; per warning rule, the event-set proposal with the catalogue to confirm; hand-written rules when compile answers `no_ai`; publish; versions list |
| `/trips/[id]` | `trips` | trip header, guests grid (deposit and balance states, guest link), cancel a day or the rest from a day, evidence with "replay a recorded warning", asks and proposals grid with the decision panel, failed actions, timeline |
| `/g/[token]` | `guest` | today's page, plus the settlement section from backend item 5 |

- `features/operator` becomes `features/trips`; `features/policy` is new.
- The sandbox notice moves to `app/layout.tsx`, so every page has it (D15) and no feature repeats it. Workspace bootstrap (`ensureWorkspace`, today in `OperatorConsole.tsx`) moves to `lib/workspace.ts` and runs before the first write on any operator page, since `/policies` is visited before any trip exists.
- API types used by more than one feature (a policy version and its products, read by the trip form) live in `lib/api-types.ts`.
- `lib/grid.tsx` registers AG Grid's community modules once and wraps `AgGridReact` with the shared column defaults; a dependency-cruiser rule keeps features from importing AG Grid directly. D10 names trips, guests and proposals tables; the timeline and failed actions are plain lists.
- The decision panel shows, per purpose, each option's label and per-guest lines (amounts from `parts`, formatted by `lib/money`), the stored question, and the draft beside it labelled "drafted by the model"; accept sends the chosen index per purpose; reject explains that it withdraws the cancellation. Facts and question are shown before the draft (threat model surface 4).
- Hand-written rules use one form per rule over the `Rule` schema (causes, scope, applies-to, remedies, chooser, quote); the quote must be pasted from the wording, and the API's span check is the only judge.
- Responsive: one column under 720 px; a grid's height follows its row count (up to 12 rows, then it scrolls), since `domLayout="autoHeight"` drew rows added after the first render over the content below on a phone (e2e); columns marked wide are left out under 720 px, so no grid scrolls sideways; actions sit outside the grids.

## Tests

- Backend integration per new route: evidence returns a replayed warning with its label and only after its visible time, and reuses a corroboration fetched within the hour; a replay opens the warning ask before it returns; failed actions grouped per (kind, subject) across all three subject kinds; the guest settlement shows this guest's lines, clause and explanation and not another guest's, 404 for an unknown token; an option deduplicated from two rules lists both. Route-table and cross-workspace 404 tests cover the new routes (existing tests, by construction).
- Web unit (vitest): option and line formatting, the guest settlement section's display per state, the trip form body with the new fields.
- e2e (Playwright, desktop 1280 px and mobile 390 px): the operator compiles or hand-writes a policy, publishes, creates a trip that starts today, invites two guests, both pay by test card, a recorded warning is replayed, the warning ask appears, the operator cancels the day, decides the settlement, and the guest page shows the cancellation, the warning with its recorded label, the clause and the outcome. The backend runs from a test-only entry point under `backend/tests/e2e/` that builds the app with `FakePayPal`, the fake model and the worker on, so no production setting can turn a fake on. `make e2e`, part of `make check`, so it runs in the pre-push gate and in CI (developer, 2026-10-20). The browser build is pinned by the `@playwright/test` version; `docs/threat-model.md` states that the download comes from Playwright's CDN, outside the lockfile and the release-age rule.
- Mutation: hide the clause on the guest page → the e2e fails.

## Units and order

1. Backend items 1–5 with their tests (one commit each for 1, 2, 3, 4, 5).
2. Dependencies, `lib/grid`, the layout notice and `lib/workspace`, the `operator` → `trips` rename, the trip form fix and the trips grid.
3. Playwright skeleton with the test-only backend: the trip-creation path green on both widths; `make e2e`.
4. `/policies`.
5. `/trips/[id]`.
6. Guest settlement section.
7. Full demo-path e2e and the mutation; responsive pass.
8. Demo operator seed after step 7's milestone (2026-10-30: a recorded warning a demo rule matches, or D4 moves PAGASA to JMA): attributed real clauses and a site in an area that warning covers, trip starting the day it is seeded.

Units 4–6 touch different features and could run in parallel; they are kept serial because each extends the e2e from unit 3.

## Unit 8: the demo operator seed (written 2026-10-25 at db9f657, before building)

D4's condition was met early (plan D4 note, 2026-10-25): the store holds PAGASA Tropical Cyclone Warnings for Super Typhoon Rosal. The judge path this unit makes possible (`docs/devpost-draft.md`, rewritten in step 12): load the demo operator, see its published rule and quoted clause, compile the same wording live to see the needs-ruling items, replay the recorded typhoon warning, cancel the rest of the trip, accept a refund, and see the refunds and the guest page.

### Where things stand

- Every browser gets its own empty workspace (`identity/sessions.py`), so a judge in a fresh browser sees no policy and no trip. Nothing seeds a workspace today.
- `warnings/catalogue.py` lists only `General Flood Advisory` for PAGASA, and says tropical-cyclone values are added once recorded.
- A replay aligns a warning's fetch time to now (backend item 3, premise), so it matches only a trip whose local day is today.

### Design

1. **Catalogue.** What the store holds (queried on the VM 2026-10-25, 30 rows): `event` is `Tropical Cyclone Warning` with no "(...)" status, the headline names the signal (`Tropical Cyclone Warning : Super Typhoon Rosal (CHAMPI) Signal #2`), stored `parameters` and `event_codes` are empty (see the next point), `severity` is Minor for Signal #1 and Moderate for #2; areas are 9-digit PSGC province codes (Batanes `020900000`, Cagayan `021500000`, Isabela `023100000`, Apayao `148100000`, Ilocos Norte `012800000`, Catanduanes `052000000`); each message is in force about 12 hours from `sent` and was fetched within 10 minutes of it. The one `Tropical Cyclone Alert` covers "Philippine Area of Responsibility" with no area code, so it can never match a trip area and is not added.
   - Every one of the 29 raw envelopes carries the signal as a CAP parameter, `Public Storm Warning Signal Level` (1 or 2; counted over the recorder's files on the VM, 2026-10-25); the parser kept only MeteoAlarm's `awareness_*` parameters, which is why the store showed none. The parser now keeps this one too, and `value_of` gives a Tropical Cyclone Warning the value `Tropical Cyclone Warning: Signal #N` from it, so matching stays membership over structured fields (`matching.py`, D16) and `value_of`'s signature and callers do not change. One without the parameter, or with a level outside 1-5, has a value outside the catalogue, so it never matches and the operator is asked. The nightly rebuild re-ingests from the envelopes, so no migration.
   - The values form one ladder, Signal #1 to #5 (PAGASA's five-level wind signal scheme; #3 to #5 are assumed to carry the same parameter, and the catalogue docstring says so). Necessary because a rule can name only catalogue values (`versions._check_rule`) and operators word thresholds as "Signal No. 2 or higher", which a ladder expresses as NWS's do.
   - The event-set proposal offers these values from the catalogue unchanged; the consumers of PAGASA values are `versions.py`, `watch.py` and `matching.py` (`rg -n "value_of\(|event_value\(|catalogue\.values" backend/src`).
2. **What the seed creates, in the caller's workspace, from fixed data in `backend/src/tidehold/bootstrap/demo.py`:**
   - A policy version written by the operator (developer, 2026-10-25: option A of three; a seed-time live compile varies run to run and needs its output patched, and a committed "recorded" compile needs `model_calls` to accept a recorded row with no live call behind it). The wording is Pure Dive Resort's published text, verbatim (`bench/fixtures/pure-dive.json`: <https://purediveresort.com/deep-diving/>, accessed 2026-10-19, lines 66-72 and 91-94); the fictional operator is "Northline Dive Co." (no real operator's name or branding). One rule, the bench's gold rule for that fixture (`bench/labels.json`, `pure-dive`) with what the operator settles written into its fields: product null; causes weather; warning named as "local authorities", source `pagasa`, event values Signal #1 to #5 (any storm warning; item 1), quote "an official storm warning issued by the local authorities"; scope per trip; applies to both payments; remedies refund 100% and credit 100% expiring after 12 months; chooser the operator; quote the two sentences from "We do not cancel" to "rescheduled or refunded." No rulings: without a compilation the API raises none (`policy/http.py` `publish`), and the seed keeps to what the API allows. Published through `versions.check` and `versions.publish`, so the span checks run as for any operator. No model call in the seed.
   - The model's part stays live: `/policies` starts its wording box with the latest published version's wording, so the judge compiles the demo wording with one click and sees the needs-ruling items the model raises (the bench's gold labels list four). This holds for any operator, not only the demo. One live call per judge, under the daily live cap of 50; past it, the same wording replays that day's result (`calls.start`).
   - **Attribution** (developer, 2026-10-25). `policy_versions` gains a nullable `quoted_from` (`{name, url, accessed}`), set only by the seed; the operator's publish route does not take it. The policy page shows it beside the version, and the guest settlement route returns it, so the guest page shows "Clause quoted from <name>'s published policy (<url>, accessed <date>)" under each clause. Necessary because the product contract allows a real operator's clause only with its source URL and access date, and the guest page quotes it; an operator's own wording leaves it null and nothing is shown. The version stays immutable (the column is written at insert).
   - A trip "Northline: Batanes weekend", two days starting today in `Asia/Manila`, warning area Batanes (`020900000`: the only province under Signal #2, and in every one of the 29 recorded warnings), site off Basco (20.45 N, 121.95 E, for the Open-Meteo forecast), deposit USD 50, balance USD 300, `balance_due_days` 30 (so the balance is due at once).
   - Two synthetic guests ("Guest A", "Guest B"), unpaid when the route returns.
   - Mandate caps at the trip's full amount. They never let this policy settle without the operator: its rule has two remedies, so every settlement asks (`evaluate.decide`); the judge sees the ask, not an automatic refund.
3. **Entry point.** `POST /api/demo-operator` (workspace class, router in `bootstrap/demo.py`; `bootstrap` sits above every module the seed calls, so no layer moves). In one transaction, under a lock on the workspace row (as `trips.store.create_trip` and the PayPal open cap take it), it refuses a workspace that already has a policy version or a trip (`Conflict("WORKSPACE_NOT_EMPTY")`), then writes the version, the trip and the guests. It returns the trip id and the guests' links. Convergence comes from the workspace's own rows, not a marker column: a second call is refused, never doubled.
4. **Payments.** The web then pays each guest's deposit and balance one request at a time through the guest page's existing route (`POST /api/guest/{token}/{purpose}/test-card`), showing "Paying Guest A's deposit (PayPal sandbox)…" and so on, and opens `/trips/{id}` when the four are done. No request waits on more than one PayPal call (an unmeasured sandbox latency times four could exceed the proxy's timeout, review finding 4), and no backend path is added. A payment that fails leaves that guest unpaid; the guests grid's link opens the guest page, whose own button pays it. Four opens, under the per-workspace PayPal cap of 30 a day.
   - **Web.** `TripsHome` shows "Load the demo operator (fictional, sandbox)", with one line on what it creates, while the workspace has neither a policy version nor a trip (the backend's condition); it calls `ensureWorkspace`, then the route, then the payments.
5. **Threat model.** One line: the seed leads to four sandbox payments per workspace, bounded by the workspace-creation cap per address (20 a day) and the per-workspace PayPal open cap; sandbox only.

### Tests

- Unit and integration (built first, with the catalogue): the committed envelope parses with its signal parameter; a stored Signal #2 row has the value `Tropical Cyclone Warning: Signal #2`, which is in the catalogue; no signal, or a level outside 1-5, is outside it; the five signals form one ladder.
- Integration: the seed in an empty workspace gives a published version whose rule span cuts the Pure Dive sentences and whose `quoted_from` is set, a trip starting today in Asia/Manila on Batanes, two unpaid guests; a second call is refused with `WORKSPACE_NOT_EMPTY` and creates nothing, and so is a call in a workspace with only a policy version; after both guests pay (FakePayPal) and the committed Signal #2 warning is replayed, the warning ask opens on the seeded trip, a whole-trip cancellation gives the refund and credit options, and the guest settlement carries `quoted_from`. A version published through the operator route has `quoted_from` null. Route-table and cross-workspace tests cover the route by construction.
- e2e: a fresh browser loads the demo operator (the payments run from the page), replays the recorded typhoon, cancels the rest of the trip, accepts the refund, and the guest page shows the clause, its attribution and the refunds; `/policies` starts with the demo wording.
- Fixture: one PAGASA Tropical Cyclone Warning envelope copied unchanged from the VM recorder to `backend/tests/fixtures/warnings/` (CC BY 4.0, listed in its README). The e2e server ingests that directory.
- Mutation: drop the signal parameter from the parser → the envelope test fails; skip the empty-workspace check → the second-call test fails; drop the attribution on the guest page → the e2e fails.

### Design review at start (2026-10-25, fresh subagent, on this section at db9f657)

| Finding | Disposition |
|---|---|
| The signal is read from the headline, while every raw envelope carries it as the CAP parameter `Public Storm Warning Signal Level`, which the parser dropped | Changed: the parser keeps it and `value_of` reads it (measured over all 29 envelopes) |
| The gold labels do not pass `versions.check` as they stand (no source, empty warning quote, no chooser, no credit expiry, rulings without questions) | Changed: the rule's final fields are written out |
| A version with no compilation carrying compiler rulings, a state the API refuses; "compiled" in the judge path | Decided by the developer: option A, an operator-written version with no rulings; the live compile on `/policies` shows the model; attribution column |
| Four PayPal calls in one request, latency not measured | Changed: the web pays through the existing guest route, one call per request |
| Empty-workspace condition differs between web and backend, and no lock | Changed: one condition, checked under the workspace row lock |
| Mandate caps said to let a settlement execute, while two remedies always ask | Changed: stated as such |
| The guest page quotes the real clause with no source | Decided by the developer: `quoted_from` shown on the guest page |

### Acceptance (with 10b's)

Shipped as a new revision with `TIDEHOLD_NEXT_REVISION=<sha> ./nightly.sh` after the developer approves; then the judge path in a fresh browser on the public URL, and the timer's own first run (10b).

## D18 screens (rewritten 2026-10-24 from "Waits for 8b", per plan step 9 Scope)

D18 removed holds, so the finalize, capture and reauthorization screens this section used to list are gone with them. What step 9 shows instead, and where each item landed:

| Item | Where | Landed |
|---|---|---|
| Guest page: each payment paid with its refunds; "Pay the balance"; no held or released-hold states | `GuestTrip.tsx`, `GuestPayments.tsx`, `GuestSettlements.tsx`, `guest/types.ts` | 8b (`fe79789`), 8c (`0cf0248`) |
| Guest page: the balance due after refunded days, and a released guest's state | `GuestTrip.tsx`, `next-payment.ts` | 8b (`fe79789`) |
| Proposal lines read "refund" for both purposes | `proposal-view.ts` | 8b (`fe79789`) |
| e2e demo path asserts refunds instead of a release | `web/e2e/demo-path.spec.ts` | 8b (`996b5fa`) |
| Rule editor shows each deduction's quote | `RemedyEditor.tsx` | 5b (`7d8dfe1`) |
| Answers for the `unpaid_balance` ask (wait, release) and the `command_open` ask (found, not found), from one table that mirrors the backend's `ANSWERS`; titles for `command_open` and a `money_refused` past the refund window | `proposal-view.ts`, `DecisionPanel.tsx` | step 9 (`cc299c6`) |
| Trip form takes `balance_due_days` (0 to 120, default 30, D20) | `TripForm.tsx`, `trip-form.ts` | step 9 (`7304d78`) |
| Guests grid: paid, refunded and credited amounts, and released guests; credit read from `GET /trips/{id}/credits`, since credits belong to `settlement` and `trips` sits below it | `GuestsSection.tsx`, `guest-rows.ts`, `trips/http.py`, `settlement/http.py` | step 9 (`c5cf5d7`) |

`GET /failed-actions` keeps finding payment actions through the guest's deposit and balance payments. The condition for moving it to a `payments.guest_id` (a guest's second payment of a purpose) does not arise under D18: each guest gets one payment per purpose at invitation, `guests.balance_payment_id` is `not null` since migration 0016, and no code reassigns either id (checked 2026-10-24: `rg -n "(deposit|balance)_payment_id\s*=" backend/src` finds only 0016's backfill).

## Recorded for later steps

- Step 10 (the measurement done 2026-10-25, no pool: plan progress row 10b; the real-phone check still open): measure where the 1-2 s per API call under `make check` came from (a new PostgreSQL connection per request, `platform/db.py`, or something else) on the VM before choosing a pool (`psycopg_pool` would be a new dependency, needing approval); recheck on a real phone the two unexplained layout symptoms (AG Grid `autoHeight` drew added rows over later content; clicks under Pixel 7 emulation landed above their target), and if found, return to `autoHeight` and device emulation.
- Step 11, when the explanation is drafted: one producer of the guest and operator wording (the operator page repeats "day N / days N to M" and refund/release in `features/trips/proposal-view.ts`), one shape of the guest view (`settlement/guest.py` dataclasses and `http.py` response models), and backend `Literal` types for states and kinds; generating the web types from FastAPI's OpenAPI (`openapi-typescript`, a new dependency) is the alternative to the hand-written mirrors.

## Design review at start (2026-10-20)

| Finding | Disposition |
|---|---|
| One `rule` index per option, re-cut from the version: options are deduplicated across rules, and `facts.clauses` is filtered by product | Changed: `rules` list into `facts.clauses`, clause from the snapshot's `quote` |
| Replay brings the due `check_trip` forward: no effect on a running row, a new row bypasses the window | Changed: the route runs the check in its own transaction |
| Replay aligns to now, so only a trip running today matches; unstated | Recorded: premise above; e2e and seed trips start today |
| Open-Meteo by UTC days, timeout mismatch, unused `corroborations` table, no shared client | Changed: trip timezone, 15 s, stored and reused for an hour, one client on `app.state` |
| Features `trips`/`policy` do not exist; notice and workspace bootstrap stuck in one feature | Changed: rename, notice in the layout, bootstrap and shared types in `lib` |
| Two new surfaces without threat-model lines | Changed: two lines in the same commits, with a trigger for the replay route |
| Failed-action route: "not recreated" is true only for `request_balance`; no join or grouping; single-value `state` parameter | Changed: `/failed-actions`, grouped, join paths stated, docstring corrected |
| Guest sentence produced in two places | Changed: backend only, one USD helper |
| `make e2e` in pre-push; browser download outside supply-chain checks | Decided by the developer: pre-push and CI; browser pinned by the Playwright version, stated in the threat model |

## Design review at the units 1-7 milestone (2026-10-20, fcb7225..07334b5)

| Finding | Disposition |
|---|---|
| Guest amounts shown only inside the explanation, which step 11's draft replaces | Changed: the sentence carries no amount; the web shows outcomes from the stored lines; threat-model line with tests |
| Failed payment actions found through the current payments only | Recorded: Waits for 8b; closed 2026-10-24 under "D18 screens" (one payment per purpose per guest) |
| 1-2 s per API call handled by a longer e2e timeout | Recorded: step 10 measurement before any pool |
| `autoHeight` and device emulation worked around, not explained | Recorded: step 10 real-phone check |
| Operator wording repeats the backend's | Recorded: step 11 |
| Hand-written web mirrors of the API types | Recorded: step 11 (backend `Literal`s, or generated types with a new dependency) |
| Guest view in two shapes (dataclasses and response models) | Recorded: step 11 |
