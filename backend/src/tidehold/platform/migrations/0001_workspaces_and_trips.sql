create table workspaces (
    id uuid primary key default gen_random_uuid(),
    -- sha256 of the session cookie; the cookie itself is never stored
    session_token_hash bytea not null unique,
    created_at timestamptz not null default now()
);

create table trips (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces (id),
    name text not null check (length(name) between 1 and 200),
    starts_on date not null,
    -- longer trips cannot settle inside one PayPal authorization (deposit/balance ADR)
    days integer not null check (days between 1 and 10),
    -- the operator re-shares the guest link, so it is stored as issued
    guest_token text not null unique,
    created_at timestamptz not null default now()
);

create index trips_workspace_id on trips (workspace_id);
