---
title: Settle on the operator's recorded cancellation, matched in code; the model works only at policy compile time
project: tidehold
date: 2026-10-19
review-date: 2027-01-19
last-reviewed:
status: active
tags: [tidehold, decision, architecture, policy, settlement]
---

# Settle on the operator's recorded cancellation, matched in code; the model works only at policy compile time

## Context

The product contract said settlements are triggered by official CAP warnings from the source the operator names, and plan step 8 was to port dive-trip-agent's runtime assessor (`assessor.py@8065eba`: a model reads free-text operator notices and proposes which trip days were cancelled and why, with citations; code computes amounts). Labelling real cancellation wording for step 2 (pages fetched 2026-10-19) showed the trigger assumption does not hold.

**Constraints:**

- `external` Real wording: of six labelled operators only Pure Dive conditions on an official warning ("unless there has been an official storm warning issued by the local authorities"), and names no service; Blue Ribbon, Scuba Tribe and Wetzone pay when *they* cancel for weather; Dive Komodo leaves it to "absolute discretion"; Mike's Dauin has no weather clause (`docs/plans/2026-10-19-tidehold-hackathon/research-operators.md`, step 2 fixtures).
- `external` Whether a boat goes out is the operator's call, and in the Philippines sailing is suspended by the Coast Guard on PAGASA wind signals, not by the warning itself ([Manila Bulletin, 2023-08-27](https://mb.com.ph/2023/8/27/sea-travel-in-northern-quezon-suspended)).
- `external` Every capture must happen before the authorization's `authorization_expires_at` (ADR 2026-10-19 deposit capture and balance hold): settlement must not depend on a model call succeeding or on free-tier quota.
- `external` CAP gives structured fields (status, msgType, references, area geocodes; `eventCode` only where the publisher sets one, e.g. NWS GLW/SCY; PAGASA puts level in the event text), CAP 1.2 §3.2 ([OASIS](https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html)).
- `external` The hackathon judges AI use (PayPal AI Hackathon rules, read 2026-10-18; the page answered 403 on 2026-10-19).
- `inherited` "The model never supplies amounts; unruled clauses stop and ask; spans are verified." Still holds: amounts must be reproducible from an immutable policy version.
- `inherited` "Only official CAP warnings trigger." Does not hold as the sole trigger: written before any operator wording was labelled.
- `inherited` Port of the runtime assessor. Does not hold: its job (reading free-text notices) has no counterpart once cancellation is a recorded action and evidence is structured CAP.

## Options Considered

- **Baseline 1. Merchant-declared cancellation**: the operator cancels a departure with a weather reason in the booking system and the platform refunds or credits per the policy, no model ([FishingBooker](https://help.fishingbooker.com/360011491174-My-trip-was-canceled-due-to-bad-weather-What-do-I-do-), [Click&Boat](https://help.clickandboat.com/hc/en-gb/articles/4416559960082-Cancel-because-of-bad-weather)).
- **Baseline 2. Parametric trigger**: an objective index from an independent publisher fires a pre-agreed payout with no assessment ([Descartes](https://descartesunderwriting.com/about/parametric-insurance)); rules are turned into code once and executed deterministically ([OECD OPSI, *Cracking the Code*, 2020](https://oecd-opsi.org/publications/cracking-the-code/)).
- **A. Current contract plus ported assessor**: CAP warnings only; at runtime a model judges event and level against the rule and proposes days and cause.
- **B. Two trigger kinds, the warning kind settling on its own**: `official_warning` rules settle when a matched warning is in force, `operator_cancellation` rules when the operator cancels (both independent drafts chose this).
- **C. Operator cancellation always, a rule may also require a warning** (chosen): settlement starts only from a recorded operator cancellation (trip day or whole trip, cause); a rule whose wording conditions on an official warning also requires a matching warning in force, matched in code. A matched warning with no cancellation becomes an operator ask drafted by the agent ("PAGASA raised Signal 2 for your area on day 3; cancel?").

## Decision

- **D1** Option C. The model runs at compile time only: wording → rules with verified spans, needs-ruling items, and a proposed closed set of the named source's event values (NWS `eventCode`, MeteoAlarm awareness type and level, PAGASA event stem) per warning condition; the operator confirms that set as part of the ruling and it is stored in the immutable policy version. It also drafts operator asks and guest explanations, which never decide money.
- **D2** Runtime matching is code: set membership, area codes, `Actual` status, Update/Cancel supersession (commit 1b2bfff). An event value outside the confirmed set never matches; if it arrives for the trip's area the agent asks the operator, proposing a mapping that the operator's confirmation adds through a new policy version.
- **D3** The assessor is not ported.

## Rationale

- C rather than B: every labelled clause that pays on weather pays when the operator cancels, and Pure Dive's warning clause is a condition on that cancellation ("In this case your trip will be either rescheduled or refunded"). Settling on a warning alone would refund guests for a day the boat went out. Cost: the demo needs one operator click after a replayed warning, and the guest page shows "cancelled by the operator" with the warnings in force rather than a payout nobody touched.
- C rather than A: matching structured CAP fields is deterministic and reproducible from (policy version, evidence); a runtime model adds quota and failure modes on the path to `authorization_expires_at` and judges something code can read. Cost: we own a per-source event catalogue, and a renamed event waits for an operator confirmation instead of being understood on the spot.
- The AI stays where judgment is needed: reading wording, finding what it leaves unruled, mapping "storm warning" to a source's values, drafting asks. That is the demo's AI moment; settlement itself looks like a rules engine, a trade-off accepted for reproducible money.

## Expected Outcome

- Settlement code has no model call; replaying (policy version, cancellations, evidence) reproduces every outcome.
- Step 2 measures compile quality (rules, needs-ruling precision and recall, event-set proposals), not runtime matching.
- A warning with no cancellation produces an ask, never money movement.

## Revocation Triggers

- A real operator wants settlement on warnings regardless of its own decision → B for that rule kind.
- Evidence arrives as free text (port notices, non-CAP feeds such as BoM RSS) → a runtime model proposing facts with citations, never amounts.
- Unmapped event values cause a missed or late settlement more than once → a runtime classifier for unknown values, operator-confirmed.

## Followup

- `AGENTS.md` product contract and Reuse, `requirements.md` (R6, R9, R10, R17, latency NFR), plan steps 2, 5, 8, 9, 11, `docs/threat-model.md` scope line: changed in the commit that adds this ADR.
- Step 5: rule schema gains trigger (operator cancellation + optional warning condition), remedies with chooser, applies-to and deductions; per-source event catalogue with ordering.
- Step 8: operator cancellation action; what happens when an ask about a matched warning is unanswered before finalize.

## Related

- Plan: `docs/plans/2026-10-19-tidehold-hackathon.md` (D4, D12, steps 2, 5, 7, 8).
- Source assessor: `Will413028/dive-trip-agent@8065eba`, `backend/src/dive_trip/modules/deposits/assessor.py`.
- Decided by the developer on 2026-10-19 after a critiqued brief and two independent drafts; this ADR is the original record.
