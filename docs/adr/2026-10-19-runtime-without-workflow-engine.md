---
title: Run tidehold's timed work on a PostgreSQL due_actions table, not a workflow engine
project: tidehold
date: 2026-10-19
review-date: 2027-04-19
last-reviewed:
status: active
tags: [tidehold, decision, architecture, scheduling]
---

# Run tidehold's timed work on a PostgreSQL due_actions table, not a workflow engine

## Context

tidehold must act at specific times per trip: request the balance hold at T-(29 − N), evaluate each trip day, reauthorize before a late capture, finalize, and settle cancellations. The money engine it reuses (`Will413028/dive-trip-agent@8065eba`, deposits module) came with a per-booking Temporal workflow that waits on a single deadline.

**Constraints:**

- `external` Deadline 2026-11-12 12:00 PT, one part-time developer: PayPal AI Hackathon rules.
- `external` Judges must see a weeks-long payment lifecycle in minutes, and tests must prove "not before T-(29 − N)": judging format and the plan's step 6 acceptance. This needs a clock the application controls.
- `external` One shared VM whose other stack (the submitted fallback demo) must not change: plan global constraints.
- `inherited` The money engine is Python/FastAPI with PydanticAI and its invariant tests (stored-before-sent commands, replace-vs-send race, money conservation, workspace isolation). Still holds: re-proving it in another language would not fit the external deadline.
- `inherited` Temporal workflow in the old repo. Does not hold for tidehold: it handles one deadline per booking, so it would be rewritten anyway; measured ≈140 Temporal-specific lines and 2 of 72 deposits tests; the engine has no Temporal import (`rg -i temporal backend/src/dive_trip/modules/deposits` → one comment).

## Options Considered

- **Baseline. PostgreSQL-backed job/schedule table claimed with `FOR UPDATE SKIP LOCKED`**: the standard for small apps with timed work — [Procrastinate](https://procrastinate.readthedocs.io/), [River](https://riverqueue.com/docs), [Oban](https://hexdocs.pm/oban); billing systems such as [Kill Bill](https://docs.killbill.io/latest/what_is_kill_bill.html) keep timers in a database queue with a movable clock for tests.
- **A. Keep Temporal**: durable timers, signals, history UI; timers follow wall-clock time (time skipping exists only in its test environment, [docs](https://docs.temporal.io/develop/python/testing-suite)); a 512 MB service on the shared VM.
- **B. Baseline implemented as a minimal `due_actions` table** (chosen): `(trip_id, kind, due_at, state, lease_until, attempts)` with an injectable clock (the source's `Workflows` Protocol was Temporal-shaped and was not ported, plan step 4).
- **C. Full TypeScript rewrite** (Next.js + Graphile Worker or Inngest): one language; rewrites the engine and its tests.
- **D. Go with River**, matching the developer's other product: full rewrite; less mature LLM structured-output tooling.

## Decision

- **D1** Python/FastAPI/PydanticAI + PostgreSQL + Next.js; time-based work in a `due_actions` table (option B), not a workflow engine and not a job library.

## Rationale

- Choose B rather than A: tidehold's flows never wait on more than one external event — stop-and-ask is a proposal row plus an HTTP approve, cancellation marks pending actions superseded — so durable workflows add little, while the demo needs a clock we can offset, which Temporal's live timers cannot give. Cost: we own leases, retries with backoff and stuck-row recovery (≈100 lines plus tests).
- Choose a hand-written table rather than Procrastinate: actions are few, coarse and derived from trip data; as our own rows they feed the audit timeline and guest page and take the injected clock directly; Procrastinate schedules on wall-clock time. Trade-off: no ready-made admin UI.
- Not C or D: one language or a lighter runtime does not outweigh rewriting and re-proving the money engine under the external deadline.
- Duplicate execution is safe because money commands are stored before sent with a stable PayPal-Request-Id and one live command per kind; a lease (`UPDATE … SET lease_until … FOR UPDATE SKIP LOCKED`, commit, then work) keeps a 30-second model call from holding a lock.

## Expected Outcome

- The demo stack has no workflow-engine service.
- Schedule tests drive an injected clock and need only PostgreSQL; the demo can advance a workspace's clock to the next action.
- On start, actions are rebuilt from trip state, so a missed action is recovered.

## Revocation Triggers

- A flow needs to wait on more than one external event, or a compensating sequence across several PayPal calls that one idempotent action cannot express → re-evaluate a workflow engine.
- The dispatcher costs more than a day of debugging, or tidehold runs on more than one worker host → re-evaluate Procrastinate or River.

## Review Notes

## Related

- Source: decided by the developer on 2026-10-19 after two independent critiques and two independent drafts; this ADR is the original record.
- Plan decision D1: `docs/plans/2026-10-19-tidehold-hackathon.md`.
- Reused engine: `Will413028/dive-trip-agent@8065eba` (`feat/paypal-deposits`).
