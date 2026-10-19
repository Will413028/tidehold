-- The server refuses a session after the cookie's 30 days, and "leave workspace" forgets it.
alter table workspaces
    add column session_expires_at timestamptz not null default now() + interval '30 days',
    alter column session_token_hash drop not null;
