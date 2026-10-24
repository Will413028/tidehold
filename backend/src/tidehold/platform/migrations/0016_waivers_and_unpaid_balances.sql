-- Refunds and credit at decision, unpaid balances (plan step 8b, D18, D21;
-- docs/plans/2026-10-19-tidehold-hackathon/step8b-design.md). A settlement's refund line on a
-- payment not yet captured lowers what the guest owes on it (a waiver), so a cancelled day is
-- never charged and then refunded. No deployment exists, so development data is converted to
-- the current shapes instead of being handled in code.

-- What a settlement (or the release of a guest) took off a payment before it was captured. One
-- per proposal and payment, so a rerun waives once; append-only like the credit ledger.
create table payment_waivers (
    id uuid primary key default gen_random_uuid(),
    payment_id uuid not null references payments (id),
    proposal_id uuid not null references proposals (id),
    amount_minor integer not null check (amount_minor between 1 and 10000000),
    created_at timestamptz not null default clock_timestamp(),
    unique (proposal_id, payment_id)
);

create function payment_waivers_append_only() returns trigger as $$
begin
    raise exception 'PAYMENT_WAIVERS_APPEND_ONLY';
end $$ language plpgsql;

create trigger payment_waivers_append_only before update or delete on payment_waivers
    for each row execute function payment_waivers_append_only();

-- the sum of the payment's waivers, kept with each under the payment's row lock: an order is
-- created for `amount_minor - waived_minor`
alter table payments
    add column waived_minor integer not null default 0,
    add constraint payments_waived check (waived_minor between 0 and amount_minor);

-- The operator released the guest from an unpaid balance (the `unpaid_balance` ask's answer):
-- it owes nothing more and is left out of every later settlement.
alter table guests
    add column released_at timestamptz,
    add column released_by uuid references proposals (id),
    add constraint guests_released check ((released_at is null) = (released_by is null));

-- Ask kinds: the unpaid balance replaces the guest without an authorization, a late payment is
-- settled by its own decision now (no ask), and an open command a read cannot decide asks.
delete from due_actions where proposal_id in (select id from proposals where kind = 'late_payment');
delete from proposals where kind = 'late_payment';
alter table proposals drop constraint proposals_kind_check;
update proposals set kind = 'unpaid_balance' where kind = 'unauthorized_guest';
alter table proposals add constraint proposals_kind_check check (kind in (
    'settlement', 'warning', 'unpaid_balance', 'money_refused', 'command_open'
));

-- A guest invited before migration 0015 gets the balance payment every guest has now.
do $$
declare
    missing record;
    created uuid;
begin
    for missing in
        select g.id, g.workspace_id, t.balance_minor
        from guests g join trips t on t.id = g.trip_id
        where g.balance_payment_id is null
    loop
        insert into payments (workspace_id, purpose, intent, amount_minor)
            values (missing.workspace_id, 'balance', 'capture', missing.balance_minor)
            returning id into created;
        insert into payment_events (payment_id, kind, detail)
            values (created, 'payment_created',
                    jsonb_build_object('purpose', 'balance', 'amount', missing.balance_minor));
        update guests set balance_payment_id = created where id = missing.id;
    end loop;
end $$;

alter table guests alter column balance_payment_id set not null;

-- A pending balance still asking PayPal for a hold, with no attempt open, is paid as a capture;
-- one placed as a hold stays `authorized` until step 8c, and settling it is refused.
update payments p set intent = 'capture'
    where p.purpose = 'balance' and p.state = 'pending' and p.intent = 'authorize'
      and not exists (
          select 1 from money_commands c
          where c.payment_id = p.id and c.kind = 'open' and c.status in ('pending', 'unknown')
      );
