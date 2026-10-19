-- The CAP lifecycle and the codes a rule matches on (CAP 1.2 §3.2.1). Only
-- `Actual` messages are evidence; an Update or Cancel replaces the messages its CAP
-- `references` name; NWS publishes event codes (GLW, SCY) that rules match instead of text.
-- Defaults cover rows recorded before this migration; re-ingesting fills them.
alter table official_warnings
    add column status text not null default 'Actual',
    add column supersedes text[] not null default '{}',
    add column event_codes jsonb not null default '{}'
        check (jsonb_typeof(event_codes) = 'object');

create index official_warnings_supersedes on official_warnings using gin (supersedes);
