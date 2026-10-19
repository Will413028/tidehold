-- PayPal money (sandbox). Amounts are USD minor units. Ported from dive-trip-agent@8065eba
-- migrations/deposits/001 and 003, keyed by payment instead of booking and diver.

-- One PayPal payment: a booking deposit (CAPTURE order, D3) or a trip balance hold
-- (AUTHORIZE order, D11). Step 6 links each guest to its two payments.
create table payments (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces (id),
    purpose text not null check (purpose in ('deposit', 'balance')),
    amount_minor integer not null check (amount_minor between 1 and 10000000),
    state text not null default 'pending'
        check (state in ('pending', 'authorized', 'captured', 'voided')),
    authorization_id text,
    authorization_expires_at timestamptz,
    capture_id text,
    captured_minor integer not null default 0 check (captured_minor >= 0),
    refunded_minor integer not null default 0
        check (refunded_minor between 0 and captured_minor),
    created_at timestamptz not null default now(),
    -- a deposit is captured outright; only a balance is ever held
    check (purpose = 'balance' or state in ('pending', 'captured')),
    check (state <> 'authorized' or authorization_id is not null),
    check (state <> 'captured' or capture_id is not null)
);

create index payments_workspace_id on payments (workspace_id);

-- Every PayPal write is stored before it is sent; its request id makes a retry return the
-- original result. `open` places the payment (checkout or the server's sandbox card); the
-- others act on what it placed and name their PayPal target.
create table money_commands (
    id uuid primary key,
    payment_id uuid not null references payments (id),
    kind text not null check (kind in ('open', 'capture', 'void', 'reauthorize', 'refund')),
    method text check (method in ('checkout', 'test_card')),
    -- the checkout order this attempt approves; set once the order exists
    order_id text check (order_id ~ '^[A-Z0-9]{8,32}$'),
    target_id text,
    amount_minor integer not null check (amount_minor >= 0),
    final_capture boolean not null default false,
    request_id text not null unique check (length(request_id) between 8 and 108),
    invoice_id text not null unique check (length(invoice_id) between 8 and 127),
    status text not null check (status in ('pending', 'succeeded', 'rejected', 'unknown')),
    attempts integer not null default 0 check (attempts >= 0),
    error_code text,
    result jsonb,
    created_at timestamptz not null default now(),
    -- first time the command went to PayPal; only an unsent checkout attempt is replaceable
    sent_at timestamptz,
    completed_at timestamptz,
    check ((kind = 'open') = (method is not null)),
    check (method = 'checkout' or order_id is null),
    check ((kind = 'open') = (target_id is null)),
    check ((status in ('succeeded', 'rejected')) = (completed_at is not null)),
    check (status in ('pending', 'rejected') or sent_at is not null)
);

-- One live (non-rejected) command per payment and step: one open attempt at a time, one
-- reauthorization (PayPal allows one from day 4 to 29), one refund, and one closing command
-- per hold: a capture or a void, never both (D11 moves balance money once).
create unique index money_commands_once on money_commands
    (payment_id, (case when kind in ('capture', 'void') then 'close' else kind end))
    where status <> 'rejected';

create index money_commands_open on money_commands (payment_id) where status in ('pending', 'unknown');

-- Append-only audit of each payment; read into the trip timeline once guests own payments.
create table payment_events (
    id bigserial primary key,
    payment_id uuid not null references payments (id),
    kind text not null check (kind ~ '^[a-z_]{3,40}$'),
    detail jsonb not null check (jsonb_typeof(detail) = 'object' and octet_length(detail::text) <= 8192),
    created_at timestamptz not null default clock_timestamp()
);

create function payment_events_append_only() returns trigger as $$
begin
    raise exception 'PAYMENT_EVENTS_APPEND_ONLY';
end $$ language plpgsql;

create trigger payment_events_append_only before update or delete on payment_events
    for each row execute function payment_events_append_only();
