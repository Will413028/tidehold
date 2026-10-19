-- Trips carry what payments and warnings need; each invited guest gets a link and owns a
-- deposit payment and, once requested, a balance payment (plan step 6). No deployed data
-- exists yet, so the new columns are added without defaults.
alter table trips
    drop column guest_token,
    add column site_lat numeric(8, 5) not null check (site_lat between -90 and 90),
    add column site_lon numeric(8, 5) not null check (site_lon between -180 and 180),
    -- the official source the operator's policy names (D4) and its area codes for the site:
    -- PAGASA SAME/PSGC, NWS UGC marine zones, MeteoAlarm EMMA_ID, JMA sea areas
    add column warning_source text not null
        check (warning_source in ('pagasa', 'nws', 'meteoalarm', 'jma')),
    add column warning_areas text[] not null
        check (cardinality(warning_areas) between 1 and 20),
    add column deposit_minor integer not null check (deposit_minor between 1 and 10000000),
    add column balance_minor integer not null check (balance_minor between 1 and 10000000),
    -- when the balance hold was opened to guests (T-(29 - N), deposit/balance ADR)
    add column balance_requested_at timestamptz,
    add constraint trips_workspace_id_id unique (workspace_id, id);

alter table payments add constraint payments_workspace_id_id unique (workspace_id, id);

create table guests (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null,
    trip_id uuid not null,
    name text not null check (length(name) between 1 and 80),
    -- the guest's own link; 24 random bytes, re-shared by the operator so stored as issued
    token text not null unique,
    deposit_payment_id uuid not null unique,
    balance_payment_id uuid unique,
    created_at timestamptz not null default now(),
    unique (trip_id, name),
    -- a guest, its trip and its payments always belong to one workspace
    foreign key (workspace_id, trip_id) references trips (workspace_id, id),
    foreign key (workspace_id, deposit_payment_id) references payments (workspace_id, id),
    foreign key (workspace_id, balance_payment_id) references payments (workspace_id, id)
);

create index guests_trip_id on guests (trip_id);
