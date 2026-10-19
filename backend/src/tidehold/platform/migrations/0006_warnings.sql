-- Official warnings as recorded (plan step 7, D4, D12, D14). A row is the evidence a decision
-- cites: what the national service published, where it was fetched and when.
create table official_warnings (
    id uuid primary key default gen_random_uuid(),
    source text not null check (source in ('pagasa', 'nws', 'meteoalarm')),
    identifier text not null check (length(identifier) between 1 and 300),
    sent timestamptz not null,
    onset timestamptz not null,
    expires timestamptz not null,
    event text not null,
    severity text not null,
    msg_type text not null,
    headline text not null,
    area_desc text not null,
    area_codes text[] not null,
    parameters jsonb not null check (jsonb_typeof(parameters) = 'object'),
    url text not null,
    -- the first recorded fetch that carried this warning
    fetched_at timestamptz not null,
    created_at timestamptz not null default now(),
    unique (source, identifier)
);

create index official_warnings_areas on official_warnings using gin (area_codes);

-- A recorded warning replayed into one workspace's demo with a fixed offset (D12): every
-- time of the warning, its fetch time included, moves by the offset, and the guest and
-- operator see it labelled "recorded at <fetched_at>, replayed".
create table warning_replays (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces (id),
    warning_id uuid not null references official_warnings (id),
    shift interval not null,
    created_at timestamptz not null default now(),
    unique (workspace_id, warning_id)
);

-- Open-Meteo marine forecast at a site: corroborates a warning, never triggers alone
-- (Open-Meteo states it is not for coastal navigation).
create table corroborations (
    id uuid primary key default gen_random_uuid(),
    site_lat numeric(8, 5) not null,
    site_lon numeric(8, 5) not null,
    day date not null,
    max_wave_height_m numeric(5, 2),
    url text not null,
    fetched_at timestamptz not null
);
