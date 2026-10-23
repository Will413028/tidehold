-- The balance is payable from the guest's invitation and falls due `balance_due_days` before
-- the first local trip day (plan step 6, D18, D20;
-- docs/plans/2026-10-19-tidehold-hackathon/step6-d18-design.md). Every guest gets its balance
-- payment at invitation, so nothing opens the balance any more. Guests invited before this
-- migration whose balance had not opened get no balance payment (developer, 2026-10-23: there
-- is no deployment yet).

-- A balance opened before this migration, never paid and never sent to PayPal, is paid as a
-- capture like every new one (the guest page's checkout now loads PayPal for CAPTURE only).
-- One with a command keeps its AUTHORIZE order, so a reconcile reads the order it placed; if
-- that command closed without placing it, the guest can pay it only with the sandbox test
-- card (local development databases only, no deployment yet).
update payments p set intent = 'capture'
    where p.purpose = 'balance' and p.state = 'pending' and p.intent = 'authorize'
      and not exists (select 1 from money_commands c where c.payment_id = p.id);

alter table trips
    add column balance_due_days integer not null default 30
        check (balance_due_days between 0 and 120),
    drop column balance_requested_at,
    -- input bounds covering the longest trips found, no longer a PayPal limit (D20)
    drop constraint trips_days_check,
    add constraint trips_days_check check (days between 1 and 21);

alter table cancellations
    drop constraint cancellations_trip_day_check,
    add constraint cancellations_trip_day_check check (trip_day between 1 and 21);

-- the kind is gone: a done or failed row would only show work nothing redoes
delete from due_actions where kind = 'request_balance';
alter table due_actions
    drop constraint due_actions_kind_check,
    add constraint due_actions_kind_check check (kind in (
        'reconcile_payment', 'check_trip', 'settle_proposal', 'draft_ask'
    ));
