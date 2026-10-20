# Step 9 design: operator console and guest page (the part 8a backs)

Plan step 9 (`../2026-10-19-tidehold-hackathon.md`), requirements R12 and R13 (`requirements.md`), decisions D10 (AG Grid), D12 (replay), D15 (do-not-enter-real-data notice) and D16. Written 2026-10-20 at fcb7225, before building; revised after the start-of-change design review (9 findings: 7 changed, 1 recorded, 1 decided by the developer; listed at the end). Step 9 starts before 8b by the developer's choice of 2026-10-20: 8b waits for step 4's day-4 reauthorization check (not before 2026-10-23 12:25 UTC). Everything here reads data 8a already stores; the finalize, capture and reauthorization screens wait for 8b (listed under "Waits for 8b").

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
5. **`GET /api/guest/{token}/settlement`** (settlement module, guest class, a second router registered under `Access.GUEST`): for each settlement proposal of the guest's trip that is not rejected, the cancellation (days, cause, recorded time), its state (`asked`: the operator is deciding; `accepted`; `executed`), the matched warnings from `facts.clauses` with source, event, fetch time and the "recorded, replayed" label, and for an accepted or executed proposal this guest's lines of the chosen option per purpose with the clauses of the option's `rules`; plus the guest's credits (amount, purpose, expiry). Each proposal carries one `explanation` sentence built by the backend ("The operator cancelled day 2 for weather. NWS's Gale Warning was in force. Your deposit: USD 25.00 refunded."); the backend is its only producer and the web only shows it, so step 11's model draft replaces the same field. USD amounts in backend text go through one helper (also used by `settlement/execute.py`'s two hand-written formats). No other guest's name or amount, and no workspace, payment or PayPal ids (the `GuestView` rule). Necessary because R12 needs these, and `holds` (where today's guest view lives) sits below `settlement` and cannot read proposals.
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

## Waits for 8b

- Finalize state per guest (captured, voided, reauthorized), the finalize proposal, refused-capture asks with the new checkout link, and the post-finalize cancellation ask: the grids and the guest page get these columns and states when 8b lands.

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
