-- Settlement (plan step 8a, D16, D17; docs/plans/2026-10-19-tidehold-hackathon/step8a-design.md):
-- the operator's cancellations, the proposals code computes from them (auto-accepted inside
-- the trip's mandate, otherwise asked), and the credit ledger. No deployed data exists yet,
-- so the new trip columns are added without defaults.

alter table trips
    -- trip days are local dates; a warning matches the local day it is in force on
    add column timezone text not null check (length(timezone) between 1 and 64),
    -- the most the trip's accepted proposals together may return (refund + release + credit)
    -- without the operator: per guest, and for the whole trip
    add column mandate_guest_minor integer not null check (mandate_guest_minor between 0 and 10000000),
    add column mandate_trip_minor integer not null check (mandate_trip_minor between 0 and 100000000),
    -- set when the operator cancels the whole trip: no guest, hold or payment starts after it
    add column cancelled_at timestamptz;

-- A trip day cancelled by the operator, or the rest of the trip from `trip_day` on.
create table cancellations (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    trip_id uuid not null,
    scope text not null check (scope in ('day', 'rest')),
    -- for `rest`, the first day it covers (days already cancelled before it are skipped)
    trip_day integer not null check (trip_day between 1 and 10),
    cause text not null check (cause in ('weather', 'safety', 'other')),
    -- the decision time: evaluation sees only evidence visible at it
    recorded_at timestamptz not null,
    -- the operator rejected its settlement: the cancellation is withdrawn and can be recorded again
    withdrawn_at timestamptz,
    unique (workspace_id, id),
    foreign key (workspace_id, trip_id) references trips (workspace_id, id)
);

-- A day is cancelled once, and a trip has at most one rest-of-trip cancellation (the store
-- refuses any cancellation after it); withdrawn ones do not count.
create unique index cancellations_one_per_day on cancellations (trip_id, trip_day)
    where withdrawn_at is null;
create unique index cancellations_one_rest on cancellations (trip_id)
    where scope = 'rest' and withdrawn_at is null;

-- What code proposes for a cancellation, or asks the operator about. One per trip, kind and
-- subject, so a check that sees the same warning twice asks once.
create table proposals (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    trip_id uuid not null,
    kind text not null
        check (kind in (
            'settlement', 'warning', 'unauthorized_guest', 'money_refused', 'late_payment'
        )),
    -- cancellation:<id> | day:<n>:<event value> | guest:<id> | command:<id> | payment:<id>
    subject text not null check (length(subject) between 1 and 300),
    cancellation_id uuid,
    state text not null
        check (state in ('asked', 'accepted', 'executed', 'rejected', 'dismissed', 'answered')),
    reasons text[] not null default '{}',
    -- per purpose: the options, each an outcome per guest computed by code
    parts jsonb not null default '{}' check (jsonb_typeof(parts) = 'object'),
    -- the option index chosen per purpose
    choice jsonb check (jsonb_typeof(choice) = 'object'),
    decided_by text check (decided_by in ('mandate', 'operator')),
    question text not null check (length(question) between 1 and 2000),
    draft text check (length(draft) <= 2000),
    draft_call_id uuid references model_calls (id),
    -- what the ask shows, as visible at its decision time (a replay's shift can change later)
    facts jsonb not null default '{}' check (jsonb_typeof(facts) = 'object'),
    created_at timestamptz not null default clock_timestamp(),
    decided_at timestamptz,
    unique (trip_id, kind, subject),
    unique (workspace_id, id),
    foreign key (workspace_id, trip_id) references trips (workspace_id, id),
    foreign key (workspace_id, cancellation_id) references cancellations (workspace_id, id),
    check ((kind = 'settlement') = (cancellation_id is not null)),
    check ((state in ('accepted', 'executed')) = (choice is not null)),
    check ((state = 'asked') = (decided_at is null))
);

-- Credit is an internal ledger record (PayPal has no voucher); append-only.
create table credits (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    guest_id uuid not null references guests (id),
    proposal_id uuid not null,
    purpose text not null check (purpose in ('deposit', 'balance')),
    amount_minor integer not null check (amount_minor between 1 and 10000000),
    expires_at timestamptz,
    created_at timestamptz not null default clock_timestamp(),
    unique (proposal_id, guest_id, purpose),
    foreign key (workspace_id, proposal_id) references proposals (workspace_id, id)
);

create function credits_append_only() returns trigger as $$
begin
    raise exception 'CREDITS_APPEND_ONLY';
end $$ language plpgsql;

create trigger credits_append_only before update or delete on credits
    for each row execute function credits_append_only();

-- The proposal that authorized a refund, void or capture; null for opening a payment.
alter table money_commands add column proposal_id uuid references proposals (id);

create index money_commands_proposal on money_commands (proposal_id) where proposal_id is not null;

alter table due_actions
    drop constraint due_actions_kind_check,
    add constraint due_actions_kind_check check (kind in (
        'request_balance', 'reconcile_payment', 'check_trip', 'settle_proposal', 'draft_ask'
    )),
    add column proposal_id uuid references proposals (id),
    drop constraint due_actions_check,
    add constraint due_actions_one_subject
        check (num_nonnulls(trip_id, payment_id, proposal_id) = 1);

drop index due_actions_one_open;
create unique index due_actions_one_open
    on due_actions (kind, coalesce(trip_id, payment_id, proposal_id))
    where state in ('pending', 'running');

alter table model_calls
    drop constraint model_calls_purpose_check,
    add constraint model_calls_purpose_check
        check (purpose in ('compile', 'event_set', 'draft_ask'));
