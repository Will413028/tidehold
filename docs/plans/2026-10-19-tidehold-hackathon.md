# tidehold: hackathon build to submission

**Goal:** By 2026-11-10 (two days before the 2026-11-12 12:00 PT deadline) a public demo where a judge compiles a dive operator's real cancellation wording into cited rules, guests hold their trip balance with PayPal sandbox authorizations, a recorded official warning (replayed per D12) triggers mandate-bounded void or partial capture and a stop-and-ask on an unruled clause, and the Devpost submission points to this repo, the demo and a <3 min video. Verified by step 14.

**Upper level:** master list is outside this repo; see the local personal context file (`AGENTS.local.md`, untracked).

**Attachments:** `2026-10-19-tidehold-hackathon/` — `requirements.md` (requirements, NFRs, interfaces, risks), `research-operators.md`, `research-ideation.md`, `feeds-matrix.md` (step 1), `scripts/check_plan.py`. Repo snapshot: see the commit that adds this plan.

## Global constraints and definition of done (stand-in; there is no master plan)

- Product contract in `AGENTS.md` is binding: model never supplies amounts; every rule cites a verified verbatim span; unruled clauses never auto-execute; CAP official warnings trigger, Open-Meteo only corroborates; holds settle inside PayPal's authorization validity; sandbox and synthetic guests only; attributed quotes and a neutral fictional demo operator.
- Provider keys and PayPal credentials stay server-side and out of git; nobody prints them; live model calls use synthetic data only.
- The dive-trip-agent fallback stack on the VM (compose project `dive-deposits-demo`, its nightly timer at 10:00 UTC, its secrets) is never modified by this plan.
- Every step after step 3 is accepted through `make check` plus the step's own commands; a step is ticked only after its acceptance ran in the orchestrator's environment; measurements and sandbox runs go to `docs/evidence.md`.
- Definition of done per product step: tests for the new behavior including one mutation that must fail; README or docs updated where behavior is user-visible; audit timeline covers new money or evidence events.

## Premises (checked 2026-10-19)

- Existing deployment changed or replaced: none for tidehold (new system). The dive-trip-agent deposits demo (https://dive-deposits.tail357fef.ts.net/deposits) stays online as the submitted fallback until step 13 and is not modified.
- Deadline 2026-11-12 12:00 PT; the submitted project is editable until then (Devpost submission page, 2026-10-18). Confirmed.
- Developer works solo, part time; the video is recorded in the developer's own voice after the product is complete (developer, 2026-10-18). Confirmed.
- Free model tiers until judging; a paid tier may be bought before judging after a benchmark (developer, 2026-10-18). Confirmed.
- The existing Groq and Gemini keys and the PayPal sandbox app credentials are host files on the VM (`${DEMO_SECRETS_DIR}/models.env`, `paypal.env`, injected into the fallback stack with `env_file`; dive-trip-agent `deploy/deposits-demo/compose.yaml`). Confirmed. Whether tidehold shares them is D13.
- PayPal authorization: valid 29 days, honor period 3 days, reauthorize only within days 4–29, capture up to 115% or +USD 75; Vault not offered to PH/ID/TH/MY merchants (PayPal docs cited in `requirements.md`). Confirmed from docs, not yet exercised.
- Prizes (rules page read 2026-10-18): AG Grid 1st USD 5,000, 2nd USD 2,000, 3rd USD 1,000 ×3; Render 1st/2nd/3rd USD 1,000/750/500 in credits; a project wins at most one grand prize or one honorable mention plus one sponsor prize. Confirmed (rules text captured in session); multiple-submission rule not yet read (step 12).
- Toolchain: Python 3.13 + uv, FastAPI, PydanticAI, PostgreSQL 18, Next.js + TypeScript + pnpm; time-based work through a PostgreSQL `due_actions` table (D1). UI and docs in English for judges (default, overridable).

**Acceptance baseline:** none (repo contains README, LICENSE, AGENTS.md, this plan).

## Invariants this phase relies on

| Invariant | Mechanisms that depend on it (planned modules; tests to port in parentheses) |
|---|---|
| The model never supplies amounts | `policy` compiler output schema, `settlement` planner, agent tool schemas |
| Every compiled rule carries a verbatim span verified against the stored wording | `policy.verify_spans`, rulings UI, guest page clause display |
| An unruled or discretionary clause never auto-executes | `settlement.mandate_check`, proposal state machine |
| A policy version is immutable and a trip binds one version | `policy` versions table trigger, `trips.policy_version_id` (port `test_workspaces_are_isolated_and_policy_is_immutable`) |
| Money commands are stored before they are sent, with a stable PayPal-Request-Id; one live command per kind per hold | `money.commands`, unique index, reconciliation (port `test_a_replacement_and_a_send_never_both_win`, `test_a_second_live_command_of_the_same_kind_is_refused`) |
| Money is conserved: captured + released = held; refunds never exceed captured | `settlement` planner (port `test_settlement_conserves_money_for_every_small_case`) |
| Every capture happens within the authorization's validity; outside the honor period a reauthorization precedes capture (D11) | `holds.schedule`, `settlement.finalize` |
| A due action takes effect at most once; a missed action is recovered from trip state on start | `due_actions` lease claim, reconcile-on-start, idempotent money commands |
| The agent sees only evidence fetched (or replay-shifted, D12) before the decision time | `warnings.store.fetched_at`, replay clock, settlement evaluation |
| Official CAP warnings trigger; Open-Meteo only corroborates | `warnings.match`, settlement evaluation |
| Workspaces are isolated; guest pages are reachable only by unguessable link | session middleware, guest token (port `test_judge_flow_and_workspace_isolation`) |
| Sandbox only | PayPal client base-URL pin and config guard |

## Scope

- Do: requirements R1–R18 in `requirements.md`.
- Don't: real operator onboarding or live PayPal (contract: sandbox only); guest accounts (link only); vouchers as PayPal objects (no native voucher); trips that cannot settle inside one authorization's validity after D11 (rejected at trip creation); dive-safety advice; Vault (not offered to the target merchant countries).
- Deferred: refund-after-capture demo beyond one example (trigger: time left after step 12); CAP regions beyond D4 (trigger: step 1 shows them reachable and time left); paid model tier (trigger: D6 before judging); tear-down of the dive-trip-agent stack (trigger: hackathon results announced).

## Steps

- [ ] **1. Spike: official feeds and recorder** (blocked by: none; target 2026-10-20; recorder runs until step 12 is done)
  - Scope: fetch and parse one live sample each from PAGASA CAP, NWS alerts, MeteoAlarm, JMA XML, BoM, BMKG, CWA; record format, area model, storm/marine event and level fields, licence, auth, and whether historical CAP archives exist. Recorder in this repo (`recorder/`, plain Python, no app dependencies) run by a systemd timer on the VM every 30 minutes, writing one file per fetch with `source`, `url`, `fetched_at`, `http_status`, raw body (non-200 kept) to a directory outside any demo DB.
  - Consumers: replay in step 7 reads the recorder's files.
  - Must not change: the fallback stack.
  - Acceptance: attachment `feeds-matrix.md` with one parsed sample per reachable source (URL, fetch time); recorder directory shows ≥4 consecutive fetches per source and every file has the four metadata fields (checked by `recorder/check.py`).
  - Milestone: by 2026-10-30 at least one recorded CAP warning that would trigger a demo rule; if none, step 7 uses the D12 fallback (official historical CAP samples with source and fetch time).
  - Stop if: no reachable source has area and storm-level fields usable for matching (then D4 changes before step 7).
  - Needs a human: CWA API key only if CWA is chosen in D4 (store locally, reply `done`).
- [ ] **2. Spike: model benchmark on free tiers** (blocked by: step 1, D13; target 2026-10-22)
  - Scope: harness in this repo (`bench/`): fetch the full cancellation pages of 6 operators from `research-operators.md` into `bench/fixtures/` with URL and access date; label expected output per case (rules, trigger source, needs-ruling list — e.g. Pure Dive's "local authorities" names no source → ruling; Mike's Dauin has no weather clause → ruling); 4 warning-to-rule matching cases from step 1 samples; a minimal span verifier reused in step 5. Candidates: Groq `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`; Gemini `gemini-3.8-flash`, `gemini-3.5-flash`, `gemini-3.1-pro-preview`, `gemma-4-31b-it`; 2 runs each. Run in a one-off container on the VM with `--env-file` pointing at the key file chosen in D13, outside 09:30–10:30 UTC.
  - Consumers: step 5 reuses the span verifier and fixtures; step 11 reads the results.
  - Must not change: the fallback stack; measured as no HTTP 429 in the fallback worker log during the benchmark window.
  - Acceptance: attachment `model-benchmark.md` with per model: structured-output success, span verification pass rate, needs-ruling recall against the labels, trigger-match accuracy, p50 and p95 latency, limits hit.
  - Stop if: no candidate reaches ≥80% structured-output success or p95 ≤60 s (then D6 considers a paid option now).
  - Needs a human: confirm the labelled expected outputs before the run.
- [ ] **3. Walking skeleton** (blocked by: D1; target 2026-10-22)
  - Scope: backend and web scaffold, pinned toolchain, `make check` (lint, typecheck, unit, integration with an isolated PostgreSQL), GitHub Actions CI on push and PR, one thin path: operator workspace session → create trip → guest link page renders the trip. Run the quality-baseline skill before the first CI.
  - Consumers: `rg -n "make check" .github docs`.
  - Must not change: `AGENTS.md` contract.
  - Acceptance: CI green on main; `make check` passes locally; integration test create-trip → guest page; mutation: break the guest-token lookup and the test fails.
- [ ] **4. Money engine port** (blocked by: step 3, D13; target 2026-10-24)
  - Scope: port from `Will413028/dive-trip-agent@8065eba` (branch `feat/paypal-deposits`): `backend/src/dive_trip/modules/deposits/{paypal,service,transactions,domain,errors}.py`, the `Workflows` Protocol seam (the Temporal workflow itself is replaced by the `due_actions` table, D1), `platform/{errors,schema,database,db_async}.py`, `modules/identity/public.py` (anonymous sessions), `bootstrap/http_boundary.py`, `migrations/deposits/*.sql` (rewritten as tidehold's own chain), test fixtures `backend/tests/conftest.py` and the compose test DB, and `src/features/deposits/PayPalCheckout.tsx`. Re-evaluate each mechanism while porting (design-review skill at start). Add the sandbox base-URL pin and config guard. Attribute the origin in README. Correct the reuse paths in `AGENTS.md` and the wiki Key Files.
  - Consumers: `rg -n "from tidehold" backend/src` after the port.
  - Must not change: the dive-trip-agent repository.
  - Acceptance: ported tests pass by name — `test_a_replacement_and_a_send_never_both_win`, `test_an_unexpected_paypal_answer_keeps_the_attempt_open`, `test_a_second_live_command_of_the_same_kind_is_refused`, `test_settlement_conserves_money_for_every_small_case`, `test_judge_flow_and_workspace_isolation`; sandbox-guard test (a non-sandbox base URL is refused); mutation: drop the sent-at guard and the race test fails; sandbox smoke (capture deposit → refund; authorize → partial capture final; authorize → void) in `docs/evidence.md`; reauthorization check: on day 0 create one card-funded and one PayPal-account authorization in a schema outside the nightly rebuild, attempt reauthorization on day 4 or later, record the result (input to the deposit/balance ADR's revocation trigger).
  - Runs on the VM in a one-off container with `--env-file` pointing at the shared sandbox credentials file (D13); credentials are never read or printed.
- [ ] **5. Policy compilation and rulings** (blocked by: step 3, D6 provisional ranking from step 2; target 2026-10-27)
  - Scope: operator pastes wording; agent produces rules (trigger: named source + event/level + area; outcome: refund %, credit, none; scope: per day / per trip) each with a verbatim span; server verifies spans; silent, discretionary or source-less clauses become "needs ruling"; operator records rulings; result is an immutable policy version. Daily live-call cap and a no-AI path (operator writes rules by hand from the same form) land here, before any public deploy.
  - Consumers: settlement (step 8), guest page (step 9).
  - Acceptance: the step-2 fixtures for Pure Dive, Blue Ribbon and Mike's Dauin run against a fixture model and match the labelled rules and needs-ruling lists; a fabricated span is rejected; mutation: skip span verification and the test fails; cap test (port `test_daily_cap_replays_recorded_results_then_stops_live_calls` adapted) and fallback test (port `test_model_failure_falls_back_to_no_ai` adapted); 3 live compiles on the VM per D13, p95 ≤60 s, in `docs/evidence.md`.
- [ ] **6. Trips, guests and balance holds** (blocked by: D3, D11, step 4; target 2026-10-28)
  - Scope: trip with site coordinates, warning area, dates, deposit and balance per guest, bound policy version; guest link; the deposit is captured through PayPal checkout at booking (D3); the balance hold is requested at T-(29 − N) for an N-day trip (immediately when the trip is closer) through PayPal JS SDK AUTHORIZE with server-card fallback for sandbox (D11); trips longer than 10 days are refused at creation. Scheduling runs on `due_actions` with an injectable clock (D1).
  - Consumers: settlement (step 8), guest page (step 9).
  - Acceptance: clock-driven tests: hold not requested before T-(29 − N) and requested on it; an 11-day trip is refused; deposit capture recorded with its money command; browser check of deposit and balance checkouts on the local stack in `docs/evidence.md`; mutation: request the hold at booking for a 60-day-out trip and the test fails.
- [ ] **7. Official warnings, corroboration and replay** (blocked by: D4, D12, step 1; target 2026-10-30)
  - Scope: CAP adapters for D4 sources; area matching to the trip's warning area; Open-Meteo corroboration at site coordinates; evidence store with `fetched_at`; replay per D12 with on-screen "recorded at … replayed" labels; demo site chosen after recording, from areas covered by a recorded triggering warning.
  - Consumers: settlement (step 8), guest page (step 9).
  - Acceptance: parser tests on recorded samples; replay test: evidence after the decision time is invisible; mutation: drop the time filter and the test fails.
- [ ] **8. Settlement within mandate, stop and ask** (blocked by: steps 5–7, D11; target 2026-11-02)
  - Scope: per trip day evaluation of the bound policy against evidence, recorded in the ledger; money moves once at finalize on the last trip day (one capture with final_capture, or a void), or immediately when the whole trip is cancelled; a guest without an authorization by the first trip day becomes an operator ask; code computes outcomes for both the balance hold (void / partial capture / capture) and the captured deposit (refund % or credit per the policy, D3); mandate check; auto-execute or ask; reauthorize before a capture outside the honor period (D11); a refused capture, reauthorization or refund turns into an operator ask; operator approve/reject; credit ledger; per-trip `due_actions` schedules with retries and backoff; audit timeline.
  - Consumers: guest page and operator console (step 9).
  - Acceptance: tests per outcome type (balance void, partial capture, capture; deposit refund, credit); unruled clause always asks; mandate cap exceeded asks; capture refused → ask; test that the last settlement action falls inside the validity for the longest allowed trip; end-to-end: new trip, real-time hold, replayed warning triggers a void, asserting hold age <29 days and evidence time < decision time; mutations: let an unruled clause execute → test fails; hold a 3-day trip so that its last day is outside the validity → test fails; settlement p95 ≤60 s on the VM in `docs/evidence.md`.
- [ ] **9. Operator console and guest page** (blocked by: D10, step 8; target 2026-11-04)
  - Scope: complete operator flow (policy, rulings, trips, guests, evidence, proposals, timeline) and guest transparency page; demo operator seed with attributed real clauses; responsive design pass; AG Grid tables if D10 = A.
  - Acceptance: unit tests for formatting and states; e2e on desktop and mobile widths for the demo path; mutation: hide the clause on the guest page and the e2e fails.
- [ ] **10. Deploy demo** (blocked by: D5, D13, D14, step 9; target 2026-11-05)
  - Scope: isolated compose project and hostname on the VM; secrets referenced by path per D13, never read; nightly void-all then rebuild at a time other than 10:00 UTC; recorded feeds mounted read-only from the D14 location; memory limits sized against the fallback stack.
  - Must not change: the fallback stack, its timer and volumes.
  - Rollback: `docker compose -p tidehold down` without touching the fallback's volumes.
  - Acceptance: public URL loads; judge path works in a fresh browser; after deploy `https://dive-deposits.tail357fef.ts.net/deposits` still returns 200; void-all leaves no open holds (sandbox query in `docs/evidence.md`); nightly job ran once.
  - Needs a human: approve the deploy; approve the new Tailscale node login if a new node is used.
- [ ] **11. Model order and reliability** (blocked by: D6, steps 2 and 10; target 2026-11-06)
  - Scope: provider order from the benchmark; confirm cap and no-AI path on the deployed demo.
  - Acceptance: with the provider key path pointed at an invalid key file (env switch), the UI shows the no-AI path; mutation: remove the cap check and the cap test fails; one live run on the demo in `docs/evidence.md`.
- [ ] **12. Devpost assets and video** (blocked by: steps 10–11; target 2026-11-08)
  - Scope: README for judges, screenshots, architecture diagram, impact text citing `research-operators.md` (public pages only, D7 = B), storyboard and rehearsal script; read and cite the rules on multiple submissions (input to D8); the developer records and uploads the video (public YouTube).
  - Acceptance: every factual claim in `docs/devpost.md` has a source line; video < 3:00.
  - Needs a human: record the voice-over, publish the video.
- [ ] **13. Submission swap** (blocked by: D8, step 12; target 2026-11-10)
  - Scope: first save the current Devpost fields (text, links, video) to `docs/devpost-previous.md`; then replace the project content with tidehold; the swap-back procedure is restoring those fields.
  - Acceptance: Devpost page shows tidehold, the repo URL, the demo URL and the new video; "Project submitted" banner present; `docs/devpost-previous.md` committed.
  - Needs a human: confirm the swap before saving.
- [ ] **14. Closeout:** evidence for each Goal element — judge path e2e output or recording on the public demo, Devpost page screenshot, video length, void-all query; definition of done per step; independent design review (design-review skill); long-lived checks in CI; deferred items with triggers moved to the master list; human tasks listed. Acceptance: `docs/evidence.md` has one entry per Goal element; `python3 docs/plans/2026-10-19-tidehold-hackathon/scripts/check_plan.py --final` exit 0 (all steps ticked, every progress row has a commit).

## Decisions

- **DA Hold window and Vault** (status: decided 2026-10-19, developer after critique; blocks: none)
  - Conclusion: the balance, not the deposit, is held by AUTHORIZE close to the trip; no Vault (not offered to target merchant countries). Exact timing is D11.
- **D1 Stack and runtime** (status: decided 2026-10-19, developer: B, with a `due_actions` table rather than a job library; ADR: `docs/adr/2026-10-19-runtime-without-workflow-engine.md`; blocks: 3)
  - A: reuse dive-trip-agent's stack including Temporal — proven, but its timers run on wall-clock time and it costs a 512 MB service on the shared VM
  - B: same but a PostgreSQL `due_actions` table instead of Temporal — the small-app baseline; the existing workflow (≈140 lines, 2 tests) needs rewriting under A as well because tidehold has several timed actions per trip; an injectable clock lets the demo and tests compress time (chosen)
- **D3 Booking deposit scope** (status: decided 2026-10-19, developer: B; ADR: `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`; blocks: 6)
  - A: deposit stays with the operator's existing process, shown as "handled by operator"; tidehold holds only the balance
  - B: deposit captured through PayPal at booking, refunded or credited per policy on a weather cancellation — full PayPal lifecycle (capture, authorize, void, partial capture, refund) and matches operators that already take PayPal deposits (chosen)
- **D4 Warning sources in v1** (status: open, after step 1; blocks: 7)
  - A: PAGASA + NWS + one more that passes step 1 (MeteoAlarm or BMKG) — three real sources; the demo's real clauses must map to a source or to "needs ruling" (recommended)
  - B: PAGASA only — fastest, contradicts "not only SEA"
  - C: all seven — broadest, risk of half-working adapters
- **D5 Demo hosting** (status: decided 2026-10-19, developer: A; blocks: 10)
  - A: the existing VM, new isolated compose project and hostname — known path, no cost (recommended)
  - B: Render — eligible for "Best Use of Render" (USD 500–1,000 in credits), new platform risk
- **D6 Model provider and paid tier** (status: open; provisional ranking after step 2, final before judging; blocks: 5, 11)
  - A: free tiers in benchmark order with fallback; buy a paid tier for the winner before judging (recommended)
  - B: free tiers only through judging
- **D7 Product-risk validation** (status: decided 2026-10-19, developer: B, nobody available to ask; blocks: none, feeds step 12)
  - A: the developer emails 3–5 operators from `research-operators.md` two questions (would you hold the balance via PayPal; how do you decide weather cancellations) — cheap, real quotes (recommended)
  - B: rely on public pages only
- **D8 Submission swap** (status: open, after step 12 reads the rules; blocks: 13)
  - A: edit the existing submitted project into tidehold, with the previous fields saved for swap-back (recommended)
  - B: a new project — depends on the multiple-submission rule
- **D10 AG Grid** (status: decided 2026-10-19, developer: A; blocks: 9)
  - A: AG Grid for trips, guests and proposals tables — eligible for the AG Grid prizes (USD 1,000–5,000); about half a day (recommended)
  - B: plain tables
- **D11 Hold timing versus trip length** (status: decided 2026-10-19, developer: A with a 10-day maximum; ADR: `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`; blocks: 6, 8)
  - A: request the hold at T-(29 − N) for an N-day trip so the last day is inside the validity, reauthorize right before any capture after the honor period, and refuse trips longer than 10 days — one hold per guest, honest limit (chosen)
  - B: hold at T-28 and capture each day non-final — keeps one request date, but day-by-day captures make guests pay before the trip ends
  - C: allow only trips of ≤2 days — simplest, excludes liveaboards
- **D12 Demo time model** (status: decided 2026-10-19, developer: A; blocks: 7, 8)
  - A: trips and holds use real time; a recorded warning is replayed with a fixed offset so it lands inside the trip, labelled "recorded at <original fetch time>, replayed" — PayPal stays real, the evidence stays authentic and labelled (recommended)
  - B: a virtual demo clock for everything — consistent, but PayPal holds cannot follow a virtual clock
  - Fallback inside A: if no triggering warning is recorded by 2026-10-30, replay an official historical CAP sample with its source and fetch time
- **D13 Credentials for tidehold** (status: decided 2026-10-19, developer: B, share the fallback's sandbox app and model keys by path; blocks: 2, 4, 10)
  - A: a new PayPal sandbox app and the same model keys, referenced from a separate secrets directory on the VM — isolates PayPal data; model quota is shared with the fallback (recommended)
  - B: share everything with the fallback stack — no setup, but both demos share the sandbox merchant and quota
  - C: new app and new model keys — full isolation, more setup
- **D14 Recorded-feed storage** (status: open, after step 1 reads licences; blocks: 10)
  - A: keep recordings on the VM outside the repo and commit only the samples used in the demo with licence lines — repo stays small, licences respected (recommended)
  - B: commit all recordings — reproducible, but large and licence varies by source

Decision order: D1, D3, D5, D7, D10, D11, D12, D13 decided 2026-10-19; D4 and D14 after step 1; D6 provisional after step 2; D8 after step 12.

## Progress

States: not started / in progress / code done, acceptance pending / done.

| Step | State | Acceptance run | Acceptance not run and why | commit |
|---|---|---|---|---|
| 1 Feeds spike and recorder | code done, acceptance pending | feeds matrix: 7 sources (9 feeds) fetched and parsed 2026-10-18; recorder unit tests 9/9, mutation (drop `http_status` from required fields) fails a test; first VM run 9/9 sources 200 | ≥4 consecutive fetches: timer started 2026-10-18 17:52 UTC, 4th run due 19:00 UTC | |
| 2 Model benchmark | not started | | | |
| 3 Walking skeleton | not started | | | |
| 4 Money engine port | not started | | | |
| 5 Policy compilation | not started | | | |
| 6 Trips and holds | not started | | | |
| 7 Warnings and replay | not started | | | |
| 8 Settlement | not started | | | |
| 9 Console and guest page | not started | | | |
| 10 Deploy | not started | | | |
| 11 Model order | not started | | | |
| 12 Devpost assets and video | not started | | | |
| 13 Submission swap | not started | | | |
| 14 Closeout | not started | | | |
