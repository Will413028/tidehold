-- Failures in a row, apart from the claim count `attempts` (which stays the lease's fencing
-- token and only grows): an action that waits on PayPal (a refund PayPal reports PENDING) is
-- read again without moving toward MAX_ATTEMPTS, and a read that succeeds resets the count
-- (plan step 4; docs/plans/2026-10-19-tidehold-hackathon/step4-d18-design.md). A separate
-- migration because 0012 already ran on databases of the step's local acceptance run.
-- Existing actions start at 0: a failed one is not run again, and a pending one gets the full
-- count of failures in a row from here.
alter table due_actions add column failures integer not null default 0 check (failures >= 0);
