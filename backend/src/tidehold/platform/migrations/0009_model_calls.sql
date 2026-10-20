-- Every model call the product makes or replays (plan step 5, threat model surface 3). Live
-- calls per UTC day are capped to protect the free-tier quota shared with the fallback demo;
-- past the cap an identical input replays the latest successful live result, else the
-- operator writes rules by hand. A started live call counts even if it never finishes.
create table model_calls (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces (id),
    purpose text not null check (purpose in ('compile', 'event_set')),
    -- the UTC day the cap counts against
    day date not null,
    mode text not null check (mode in ('live', 'recorded', 'capped')),
    status text not null check (status in ('started', 'succeeded', 'failed')),
    -- sha256 over purpose, exact model ID, prompt hash and input: equal digests replay
    input_digest text not null check (input_digest ~ '^[0-9a-f]{64}$'),
    model text not null check (length(model) between 1 and 100),
    prompt_hash text not null check (prompt_hash ~ '^[0-9a-f]{16}$'),
    replay_of uuid references model_calls (id),
    result jsonb check (jsonb_typeof(result) = 'object'),
    error_code text check (length(error_code) <= 200),
    created_at timestamptz not null default clock_timestamp(),
    finished_at timestamptz,
    unique (workspace_id, id),
    check ((mode = 'recorded') = (replay_of is not null)),
    check ((status = 'succeeded') = (result is not null)),
    check ((status = 'started') = (finished_at is null)),
    check (mode = 'live' or status <> 'started')
);

create index model_calls_live_per_day on model_calls (day) where mode = 'live';
create index model_calls_replayable on model_calls (input_digest, created_at)
    where mode = 'live' and status = 'succeeded';

-- The compilation a version was confirmed from; null when the operator wrote it by hand.
alter table policy_versions
    add column compilation_id uuid,
    add constraint policy_versions_compilation
        foreign key (workspace_id, compilation_id) references model_calls (workspace_id, id);
