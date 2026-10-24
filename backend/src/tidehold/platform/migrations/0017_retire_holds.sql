-- Retire the authorization paths (plan step 8c, D18): a payment is a CAPTURE order the guest
-- pays, and money goes back only as refunds. Design:
-- docs/plans/2026-10-19-tidehold-hackathon/step8c-design.md.

-- No database we keep holds a hold (there is no deployment, and the development database is
-- recreated on each start), so nothing is converted: a database that still has one stops here.
do $$
begin
    if exists (select 1 from payments where state not in ('pending', 'captured') or intent <> 'capture')
        or exists (select 1 from money_commands where kind not in ('open', 'refund'))
    then
        raise exception 'HOLD_ROWS_PRESENT: recreate the database';
    end if;
end
$$;

-- Dropping the hold's columns drops the checks that name them (`payments_intent_purpose`,
-- `payments_authorized_intent`, `state <> 'authorized' or authorization_id is not null`).
alter table payments
    drop column intent,
    drop column authorization_id,
    drop column authorization_expires_at,
    -- "only a balance is ever held": nothing is. PostgreSQL named 0003's unnamed checks in
    -- order: `payments_check` is the refund bound and stays, `payments_check1` is this one.
    drop constraint payments_check1,
    drop constraint payments_state_check,
    add constraint payments_state_check check (state in ('pending', 'captured'));

drop index money_commands_one_reauthorization_in_flight;
-- One live (non-rejected) open attempt per payment; refunds stay bounded in code under the
-- payment's lock and by `payments_check` (`refunded_minor between 0 and captured_minor`).
drop index money_commands_once;
create unique index money_commands_once on money_commands (payment_id)
    where kind = 'open' and status <> 'rejected';

alter table money_commands
    drop constraint money_commands_kind_check,
    add constraint money_commands_kind_check check (kind in ('open', 'refund')),
    -- the only command of no amount was the void
    drop constraint money_commands_amount_minor_check,
    add constraint money_commands_amount_minor_check check (amount_minor > 0);
