-- When reconciliation last read PayPal's record of a sent command (plan step 4, second review;
-- docs/plans/2026-10-19-tidehold-hackathon/step4-d18-design.md). A settlement waiting on such a
-- command treats a failed `reconcile_payment` as "the reads gave up" only when that action read
-- this command; one that failed before reading it (the command was sent during its last run, so
-- the read's wake was dropped) is followed by a fresh read. Existing commands start unread.
alter table money_commands add column read_at timestamptz;
