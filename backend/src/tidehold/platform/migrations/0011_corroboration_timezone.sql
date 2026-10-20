-- A forecast's daily maximum is per local day of the trip (plan step 9; trip days are local
-- since step 8a), so the stored days carry the time zone they were asked in. The table has had
-- no writer before this step, so the column is added without a default.
alter table corroborations add column timezone text not null;

create index corroborations_recent on corroborations (site_lat, site_lon, timezone, fetched_at);
