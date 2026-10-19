-- Timed work (D1): claimed under a lease, so a stalled worker's action is taken over once.
create table due_actions (
    id bigserial primary key,
    kind text not null check (kind in ('request_balance', 'reconcile_payment')),
    trip_id uuid references trips (id),
    payment_id uuid references payments (id),
    due_at timestamptz not null,
    state text not null default 'pending' check (state in ('pending', 'running', 'done', 'failed')),
    lease_until timestamptz,
    attempts integer not null default 0 check (attempts >= 0),
    last_error text check (length(last_error) <= 500),
    created_at timestamptz not null default now(),
    finished_at timestamptz,
    check ((trip_id is null) <> (payment_id is null)),
    check ((state = 'running') = (lease_until is not null)),
    check ((state in ('done', 'failed')) = (finished_at is not null))
);

-- One open action per kind and subject; scheduling the same work again is a no-op.
create unique index due_actions_one_open on due_actions (kind, coalesce(trip_id, payment_id))
    where state in ('pending', 'running');

create index due_actions_claimable on due_actions (due_at) where state in ('pending', 'running');
