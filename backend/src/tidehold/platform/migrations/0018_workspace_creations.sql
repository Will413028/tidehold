-- Workspace creations per client address, for the per-address daily cap on the public demo
-- (plan step 10a, D15; docs/plans/2026-10-19-tidehold-hackathon/step10a-design.md). The count
-- lives in the database so an API restart does not reset it; the nightly rebuild empties it.
create table workspace_creations (
    id bigserial primary key,
    -- an IPv4 address, an IPv6 /64, or 'unknown' (identity/sessions.py `address_key`)
    address_key text not null check (length(address_key) between 1 and 64),
    created_at timestamptz not null default now()
);

create index workspace_creations_address on workspace_creations (address_key, created_at);
