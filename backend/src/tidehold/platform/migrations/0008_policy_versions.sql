-- A policy version: the operator's wording and the rules and rulings the operator confirmed
-- from it (plan step 5, D16). Fixed once written, so a trip's outcomes can be recomputed
-- from the version it binds. No deployed data exists yet, so trips get the binding without
-- a default.
create table policy_versions (
    id uuid primary key default gen_random_uuid(),
    workspace_id uuid not null references workspaces (id),
    wording text not null check (length(wording) between 1 and 12000),
    -- [{rule, span: [start, end], warning_span: [start, end] | null}], offsets into wording
    rules jsonb not null check (jsonb_typeof(rules) = 'array'),
    -- [{topic, question, quote, span, answer}]: each item the compiler raised, answered
    rulings jsonb not null check (jsonb_typeof(rulings) = 'array'),
    -- the one source every warning condition names; null when no rule has one
    warning_source text check (warning_source in ('pagasa', 'nws', 'meteoalarm', 'jma')),
    created_at timestamptz not null default now(),
    unique (workspace_id, id)
);

create index policy_versions_workspace_id on policy_versions (workspace_id);

create function policy_versions_immutable() returns trigger as $$
begin
    raise exception 'POLICY_VERSION_IMMUTABLE';
end;
$$ language plpgsql;

create trigger policy_versions_immutable before update or delete on policy_versions
    for each row execute function policy_versions_immutable();

alter table trips
    add column policy_version_id uuid not null,
    -- the product heading whose rules apply, when the version has more than one product
    add column policy_product text check (length(policy_product) between 1 and 200),
    add constraint trips_policy_version
        foreign key (workspace_id, policy_version_id) references policy_versions (workspace_id, id);

create function trips_policy_bound() returns trigger as $$
begin
    raise exception 'TRIP_POLICY_BOUND';
end;
$$ language plpgsql;

create trigger trips_policy_bound before update of policy_version_id, policy_product on trips
    for each row
    when (old.policy_version_id is distinct from new.policy_version_id
          or old.policy_product is distinct from new.policy_product)
    execute function trips_policy_bound();
