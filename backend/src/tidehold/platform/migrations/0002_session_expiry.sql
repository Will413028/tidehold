-- The server refuses a session after the cookie's 30 days, and "leave workspace" forgets it.
alter table workspaces
    -- set by create_workspace from SESSION_SECONDS, the cookie's lifetime
    add column session_expires_at timestamptz not null,
    alter column session_token_hash drop not null;
