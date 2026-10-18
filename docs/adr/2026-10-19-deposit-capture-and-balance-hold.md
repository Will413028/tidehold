---
title: Capture the deposit at booking and hold the balance at T-(29 − N), trips up to 10 days
project: tidehold
date: 2026-10-19
review-date: 2027-01-19
last-reviewed:
status: active
tags: [tidehold, decision, payments, paypal]
---

# Capture the deposit at booking and hold the balance at T-(29 − N), trips up to 10 days

## Context

A dive trip booking has a deposit taken at booking and a balance due before the trip; weather cancellations then decide what each guest owes. tidehold has to move both through PayPal so that the money taken matches the operator's compiled policy.

**Constraints:**

- `external` PayPal authorization: valid 29 days, 3-day honor period; reauthorize only after the honor period and inside the 29 days, each reauthorization getting a new ID without extending the 29 days; capture up to 115% or +USD 75 ([authorization guide](https://developer.paypal.com/docs/checkout/standard/customize/authorization/), [reauthorize](https://developer.paypal.com/docs/api/payments/v2/authorizations-reauthorize), read 2026-10-19).
- `external` The reauthorize reference describes "an authorized PayPal account payment"; support for card-funded authorizations is unconfirmed.
- `external` Vault (saved payment methods) is not offered to merchants in the Philippines, Indonesia, Thailand or Malaysia, so nothing can be charged without the guest present ([cards](https://developer.paypal.com/docs/checkout/save-payment-methods/during-purchase/js-sdk/cards/)).
- `external` Judging weighs how thoroughly PayPal is used (Technological Implementation); several real operators already take PayPal deposits (`docs/plans/2026-10-19-tidehold-hackathon/research-operators.md`).
- `external` Sandbox only; the demo is rebuilt nightly after voiding every open hold.

## Options Considered

- **Baseline. Save the payment method at booking and charge or authorize near arrival without the guest** ([Stripe saved payment methods](https://docs.stripe.com/payments/save-and-reuse), [Airbnb pay part now, part later](https://www.airbnb.com/help/article/2128)): unavailable here because Vault is not offered in the target merchant countries.
- **A. Deposit captured through PayPal at booking; balance AUTHORIZE at T-(29 − N); trips ≤ 10 days** (chosen).
- **B. Deposit stays with the operator; tidehold holds only the balance.**
- **C. Hold at T-28 and capture each day non-final.**
- **D. Capture the full price upfront and refund on cancellation.**
- **E. Allow only trips of ≤ 2 days.**

## Decision

- **D3** The deposit is an Orders v2 CAPTURE at booking; on a weather cancellation it is refunded through PayPal or recorded as credit, per the compiled policy.
- **D11** The balance is an AUTHORIZE requested at T-(29 − N) for an N-day trip (immediately when booked later), so finalizing on the last trip day lands on day 28 of the authorization at the latest. Daily evaluations write outcomes to the ledger; money moves once at finalize (one capture with `final_capture=true`, or a void), or immediately when the whole trip is cancelled. A capture after the honor period is preceded by a reauthorization. Trips longer than 10 days are refused at creation. A guest who has not authorized by the first trip day becomes an operator ask.
- Failures: unknown outcomes reconcile under the same PayPal-Request-Id; a refused reauthorization falls back to capturing against the original, still-valid authorization; a refused capture or refund becomes an operator ask with a new checkout link as recovery. No automatic retry of a refusal.

## Rationale

- Choose A rather than B: B (argued by one independent draft as the realistic default) leaves the deposit outside the system, so code cannot execute the deposit refund or credit the policy decides, and the demo shows less of PayPal; A matches operators that already take PayPal deposits. Cost: money is taken months ahead, and a deposit refund does not return the operator's PayPal fee (to verify on PayPal's fee page before stating it publicly) — which is exactly why the balance is held rather than charged.
- Choose T-(29 − N) rather than C: C makes guests pay during the trip, before the weather outcome is known. Rather than E: E excludes liveaboards, the trips weather hits hardest. Rather than D: D charges everything before the weather is known and refunds lose fees.
- Cost of A: the request date varies with trip length; trips over 10 days are excluded; capture risk concentrates near the end of the validity, and card reauthorization may not be available.

## Expected Outcome

- Every settlement finishes inside one authorization; a test fails if the last action of a 10-day trip falls after day 28.
- One PayPal write per guest at finalize for the balance; the deposit refund or credit is visible on the guest page.

## Followup

- Sandbox check before step 8: create a card-funded and a PayPal-account authorization kept outside the nightly rebuild, then attempt reauthorization on day 4 or later (plan step 4).
- Live demo cannot show a reauthorization (holds are voided nightly); show it from the recorded sandbox check, labelled as recorded.

## Revocation Triggers

- The sandbox refuses card reauthorization and declines captures after the honor period → move evaluations inside the honor period or ask the operator earlier.
- Operators need trips longer than 10 days, or Vault becomes available in these markets → re-evaluate the baseline.

## Review Notes

## Related

- Source: decided by the developer on 2026-10-19 after a concept critique, a plan review and two independent drafts; this ADR is the original record.
- Plan decisions D3 and D11: `docs/plans/2026-10-19-tidehold-hackathon.md`.
