-- Capture and refunds only (plan step 4, D18; docs/plans/2026-10-19-tidehold-hackathon/step4-d18-design.md).
-- Additive: the hold paths (AUTHORIZE, capture of an authorization, void, reauthorize) stay
-- until plan step 8c, because step 8a's settlement still runs them.

alter table payments
    -- what the payment's order asks PayPal for: every deposit is captured at once; a balance
    -- created before D18 is held (step 6 creates captured balances)
    add column intent text check (intent in ('capture', 'authorize')),
    -- the PayPal order that placed the payment; reads of its captures and refunds go through it
    add column order_id text check (order_id ~ '^[A-Z0-9]{8,32}$'),
    -- when the capture was first sent to PayPal, so never later than PayPal's own capture
    -- time: PayPal accepts a refund for 180 days after the capture, and a window counted from
    -- here closes no later than PayPal's
    add column captured_at timestamptz;

update payments set intent = case purpose when 'deposit' then 'capture' else 'authorize' end;
-- the command that captured: the open of a captured order, or the capture of a hold
update payments p set captured_at = c.sent_at
    from money_commands c
    where c.payment_id = p.id and c.status = 'succeeded' and p.state = 'captured'
      and c.kind = case p.intent when 'capture' then 'open' else 'capture' end;
update payments p set order_id = coalesce(c.order_id, c.result ->> 'orderId')
    from money_commands c
    where c.payment_id = p.id and c.kind = 'open' and c.status = 'succeeded';

-- no default: whoever creates a payment says what its order asks for
alter table payments
    alter column intent set not null,
    add constraint payments_intent_purpose check (intent = 'capture' or purpose = 'balance'),
    add constraint payments_authorized_intent check (intent = 'authorize' or state <> 'authorized'),
    add constraint payments_captured_at check (state <> 'captured' or captured_at is not null),
    add constraint payments_order_placed check (state = 'pending' or order_id is not null);

alter table money_commands
    -- any attempt keeps the order it created, a test-card attempt too
    drop constraint money_commands_check1,
    add constraint money_commands_order_of_open check (kind = 'open' or order_id is null),
    -- PayPal refused a resend: the first send may have moved money, so it stays open
    add column resend_refused_at timestamptz;

-- A payment may be refunded several times (D18); its refunds are bounded in code under the
-- payment's row lock and by `refunded_minor <= captured_minor`. What stays one at a time: the
-- open attempt and, until step 8c, the hold's closing capture or void.
drop index money_commands_once;
create unique index money_commands_once on money_commands
    (payment_id, (case when kind in ('capture', 'void') then 'close' else kind end))
    where status <> 'rejected' and kind not in ('reauthorize', 'refund');

-- One live refund per settlement and payment: a retried or concurrent settlement cannot
-- refund the same payment twice for the same decision. A refund without a proposal (the
-- sandbox smoke) is bounded only by the capture.
create unique index money_commands_one_refund_per_proposal on money_commands
    (proposal_id, payment_id)
    where kind = 'refund' and status <> 'rejected';
