---
title: Collect the balance before the trip and settle weather outcomes as refunds or credit
project: tidehold
date: 2026-10-23
review-date: 2027-01-23
last-reviewed:
status: active
supersedes: 2026-10-19-deposit-capture-and-balance-hold
tags: [tidehold, decision, payments, paypal]
---

# Collect the balance before the trip and settle weather outcomes as refunds or credit

## Context

The superseded ADR held each guest's balance as a PayPal AUTHORIZE at T-(29 − N) and captured it once near day 28 (D11). Designing step 8b showed that every open question came from that one choice: reauthorization after the 3-day honor period and its fallback, a finalize time that lands inside the last local day west of UTC, operator asks still open when the hold must be captured, a second payment after a refused late capture, cancellations after finalize, a hold-age guard, the 10-day trip limit, and step 4's day-4 sandbox check as a blocker. PayPal's own pages also disagree on reauthorization (once vs several times; on failure "void the original authorization and request new payment" vs capturing the original).

**Constraints:**

- `external` Vault (saved payment methods) is not offered to merchants in the Philippines, Indonesia, Thailand or Malaysia, so nothing can be charged without the guest present ([cards](https://developer.paypal.com/docs/checkout/save-payment-methods/during-purchase/js-sdk/cards/)).
- `external` A refund does not return the fee PayPal took on the payment: "the fees you originally paid to receive the payment are not returned" ([PayPal fees](https://www.paypal.com/us/business/paypal-business-fees), read 2026-10-23). PayPal's US user agreement forbids a surcharge "for accepting PayPal" ([user agreement](https://www.paypal.com/us/legalhub/paypal/useragreement-full)); regional versions not checked.
- `external` Operators in the target markets collect the balance 30 to 45 days before the trip and settle weather cancellations by refund or credit (`docs/plans/2026-10-19-tidehold-hackathon/research-operators.md` #4, #6, #7, #14); liveaboards 60 to 90 days ([Aquatic Apes](https://aquaticapes.com/pages/cancellation-policy), [Mermaid](https://www.mermaid-liveaboards.com/bookingpolicy/)).
- `external` Where package-travel law applies, an organiser that cancels for unavoidable and extraordinary circumstances owes "a full refund of any payments made" within 14 days (EU 2015/2302 Art. 12(3)–(4), [text](https://www.legislation.gov.uk/eudr/2015/2302/article/12)); the target countries' rules were not found.
- `external` Judging weighs how thoroughly PayPal and AI are used, equally with impact, design, innovation and presentation ([rules](https://paypalaihackathon.devpost.com/rules)).
- `external` Sandbox only; deadline 2026-11-12.
- `inherited` "The balance is held, not charged, until the weather is known" (README, plan Goal, `AGENTS.md`). Does not hold: a product assumption never validated with an operator, and the operators researched do the opposite.
- `inherited` Steps 4 and 6 are built around the hold (code done, acceptance pending). Not a reason: code we own.

## Options Considered

- **Baseline. Save the payment method at booking and charge the balance on its due date without the guest; refund or credit per the policy on cancellation** ([Stripe saved payment methods](https://docs.stripe.com/payments/save-and-reuse), [Airbnb pay part now, part later](https://www.airbnb.com/help/article/2128)): unavailable because Vault is not offered in the target markets.
- **A. Keep D11**: balance AUTHORIZE at T-(29 − N), one capture or void near day 28, reauthorization after the honor period; trips ≤ 10 days.
- **B. Hold only inside the honor period** (old option E): trips of at most 2 days, or guests approve during the trip.
- **C. The baseline without Vault** (chosen): the guest pays the balance on its due date from a payment request; weather outcomes become refunds or credit as each cancellation is decided.

## Decision

- **D1** The deposit is captured at booking (unchanged from the superseded D3). The balance is captured when the guest pays it; it falls due 30 days before the first trip day by default, operator-adjustable, and at once for later bookings. An unpaid balance by its due date becomes an operator ask. No AUTHORIZE, void or reauthorization remains.
- **D2** Each accepted settlement executes when it is decided: a refund of the captured deposit or balance, or a credit, as the compiled policy says. A payment may be refunded several times; the refunds of a payment never exceed what it captured (`payments.refunded_minor <= captured_minor`), replacing "one refund per payment". A refused refund stays an operator ask.
- **D3** Deductions on a refund come only from the operator's wording, each with its own verified span; wording that deducts without a figure, deducts on a condition the schema cannot express (for example "for online payments"), or conflicts with a full-refund promise becomes a ruling. Without a deduction the guest gets the full amount and the operator absorbs PayPal's unreturned fee.
- **D4** An unknown or refused-on-resend money command is resolved by reading PayPal's state (order, capture, refund) before any operator ask.
- **D5** The trip-length limit and the trip-end finalize go away. How the guest pays the balance (the guest page's PayPal checkout, or a PayPal invoice with due date and reminders) waits on a sandbox check that an invoice payment can be partially refunded through the captures refund API.

## Rationale

- C rather than A: A carries reauthorization behaviour PayPal documents inconsistently, a capture deadline that forces decisions on unanswered asks, and mechanisms (finalize scheduling, hold-age guard, second payment, after-finalize asks) that exist only because money waits 28 days; C settles each decision when it is made, like the operators it serves. Cost: on a weather refund the operator loses PayPal's fee, which a void under A would have avoided; the pitch line "held, not charged" goes.
- C rather than B: B excludes liveaboards, the trips weather hits hardest.
- D3 rather than a product-level fee setting: amounts already come only from the operator's verified wording; a separate setting would bypass it. Cost: an operator whose wording deducts fees on its own weather cancellations may breach package-travel law where that law applies; tidehold follows the wording and does not judge it.
- D4 rather than an operator ask first: both PayPal and Stripe direct merchants to read the provider's record before acting on an unknown outcome ([PayPal](https://www.paypal.com/us/cshelp/article/how-do-i-handle-paypal-api-timeouts-and-internal-server-error-messages-ts1444), [Stripe](https://docs.stripe.com/error-low-level)).

## Expected Outcome

- No settlement depends on an authorization's validity; step 8b's open questions on reauthorization, finalize time and unanswered asks at finalize no longer arise.
- A test refunds one payment twice and refuses a third refund that would exceed the captured amount.
- Scuba Tribe's clause compiles to a refund of 100% less 3.5% for online payments as a ruling, or a reschedule as credit, chosen by the guest.

## Followup

- Plan: rewrite premises, invariants, scope and steps 4, 6, 8, 8b, 9, 10, 12, 14; add the invoice sandbox check before the balance payment work.
- `AGENTS.md` product contract, README, `requirements.md`, `docs/threat-model.md`; settlement ADR D3 amended.
- Remove the authorization paths (`holds/schedule.py` T-(29 − N), AUTHORIZE, void, reauthorize, the reauthorization unique index, `bootstrap/sandbox.py` reauthorization check).

## Amendment (2026-10-23): D5's open items decided

- **Balance channel:** the guest page's PayPal checkout. Invoicing v2 is not enabled on the sandbox app shared with the fallback demo (measured on 2026-10-23: no invoicing scope in the token, `403 NOT_AUTHORIZED` on listing invoices), and the developer chose not to change that app. Invoice reminders would let PayPal chase late payers; that waits on an operator asking for it.
- **Bounds:** trips of 1 to 21 days and balances due 0 to 120 days before the trip. These are input bounds covering the largest values found: liveaboards of about two weeks, and balances due 30 to 105 days ahead. They are not payment limits. PayPal accepts refunds within 180 days of the capture ([Checkout refunds](https://developer.paypal.com/checkout/refund-payment), read 2026-10-23). No bound keeps a refund inside that window, because a guest may pay from invitation and liveaboards take deposits months ahead. So a refund past the window is refused before it is sent and asks the operator with that reason.
- **Late and unpaid payments:**
  - The balance is charged net of refunds already decided for it, so a cancelled day is never charged and then refunded, and no PayPal fee is lost on it.
  - A payment still uncovered gets its own settlement proposal from the original's choice and policy version, through the same mandate check. This replaces "settle it outside tidehold".
  - Completing a checkout takes the trip lock that recording a cancellation takes.
  - An unpaid balance asks the operator to wait or to release the guest. Releasing moves no money, because guest-cancellation clauses are not compiled. Operators researched "reserve the right" to treat an unpaid balance as the guest's cancellation (Komodo Cruises, Dragon Dive).
  - The ask offers no due-date extension: choosing to wait keeps the payment link open.

## Revocation Triggers

- Vault becomes available in the target markets → the baseline (charge the saved method on the due date).
- An operator asks that the balance not be charged before the weather is known → reconsider a short hold for that operator.
- A target market's law forbids deductions on operator cancellations → such deductions become rulings regardless of wording.

## Related

- Supersedes `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`; amends `docs/adr/2026-10-19-settlement-triggers-and-model-role.md` D3.
- Decided by the developer on 2026-10-23 after three critiqued briefs (balance model, fee bearer, positioning); this ADR is the original record.
