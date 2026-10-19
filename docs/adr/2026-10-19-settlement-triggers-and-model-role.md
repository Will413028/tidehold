---
title: Settle on the operator's recorded cancellation, matched in code; no model call decides a settlement
project: tidehold
date: 2026-10-19
review-date: 2027-01-19
last-reviewed:
status: active
tags: [tidehold, decision, architecture, policy, settlement]
---

# Settle on the operator's recorded cancellation, matched in code; no model call decides a settlement

## Context

The product contract said settlements are triggered by official CAP warnings from the source the operator names, and plan step 8 was to port dive-trip-agent's runtime assessor (`assessor.py@8065eba`: a model reads free-text operator notices and proposes which trip days were cancelled and why, with citations; code computes amounts). Labelling real cancellation wording for step 2 (pages fetched 2026-10-19) showed the trigger assumption does not hold.

**Constraints:**

- `external` Real wording (`docs/plans/2026-10-19-tidehold-hackathon/research-operators.md`, read 2026-10-18; cancellation sections re-read 2026-10-19): of nine operators two condition on official information and neither names a service: Pure Dive ("unless there has been an official storm warning issued by the local authorities. In this case your trip will be either rescheduled or refunded") and Kaia ("tour cancellation due to weather conditions is accepted based on official information … a subjective assessment of weather conditions by trip participants cannot be the basis"); Blue Ribbon, Scuba Tribe, Wetzone and Aquatic Apes pay when *they* cancel; Dive Komodo leaves it to "absolute discretion"; Dragon Dive refuses refunds when the park closes for weather; Mike's Dauin has no weather clause. In every case the operator decides or accepts the cancellation.
- `external` Whether a boat goes out is the operator's call, and in the Philippines sailing is suspended by the Coast Guard on PAGASA wind signals, not by the warning itself ([Manila Bulletin, 2023-08-27](https://mb.com.ph/2023/8/27/sea-travel-in-northern-quezon-suspended)).
- `external` Every capture must happen before the authorization's `authorization_expires_at` (ADR 2026-10-19 deposit capture and balance hold): settlement must not depend on a model call succeeding or on free-tier quota.
- `external` CAP gives structured fields (status, msgType, references, area geocodes; `eventCode` only where the publisher sets one, e.g. NWS GLW/SCY; PAGASA puts level in the event text), CAP 1.2 §3.2 ([OASIS](https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html)).
- `external` The hackathon judges AI use ([PayPal AI Hackathon rules](https://paypalaihackathon.devpost.com/rules), read 2026-10-18; the page answered 403 on 2026-10-19).
- `inherited` "The model never supplies amounts; unruled clauses stop and ask; spans are verified." Still holds: amounts must be reproducible from an immutable policy version.
- `inherited` "Only official CAP warnings trigger." Does not hold as the sole trigger: written before any operator wording was labelled.
- `inherited` Port of the runtime assessor. Does not hold: its job (reading free-text notices) has no counterpart once cancellation is a recorded action and evidence is structured CAP.

## Options Considered

- **Baseline 1. Merchant-declared cancellation**: the operator cancels a departure with a weather reason in the booking system and the platform refunds or credits per the policy, no model ([FishingBooker](https://help.fishingbooker.com/360011491174-My-trip-was-canceled-due-to-bad-weather-What-do-I-do-), [Click&Boat](https://help.clickandboat.com/hc/en-gb/articles/4416559960082-Cancel-because-of-bad-weather)).
- **Baseline 2. Parametric trigger**: an objective index from an independent publisher fires a pre-agreed payout with no assessment ([Descartes](https://descartesunderwriting.com/about/parametric-insurance)); rules are turned into code once and executed deterministically ([OECD OPSI, *Cracking the Code*, 2020](https://oecd-opsi.org/publications/cracking-the-code/)).
- **A. Current contract plus ported assessor**: CAP warnings only; at runtime a model judges event and level against the rule and proposes days and cause.
- **B. Two trigger kinds, the warning kind settling on its own**: `official_warning` rules settle when a matched warning is in force, `operator_cancellation` rules when the operator cancels (both independent drafts chose this).
- **C. Baseline 1 with an optional code-matched warning condition** (chosen): settlement starts only from a cancellation the operator records (a trip day or the whole trip, its cause, and whether a guest asked for it); a rule whose wording conditions on an official warning also requires a matching warning, matched in code. A matched warning with no cancellation becomes an operator ask ("NWS issued a Gale Warning for your area on day 3; cancel?").

## Decision

- **D1** Option C. The model compiles wording into rules with verified spans, needs-ruling items, and a proposed set of the named source's event values (from the per-source catalogue in `warnings/catalogue.py`: NWS event types, MeteoAlarm awareness type and colour, PAGASA event stems); the operator confirms the set as part of the ruling and it is stored in the immutable policy version. At runtime the model only drafts operator asks and guest explanations, each with a template fallback; neither decides money, and settlement makes no model call.
- **D2** A warning condition holds when a warning with a value in the confirmed set, for the trip's area codes, is `Actual`, not replaced by an Update or Cancel (commit 1b2bfff), visible when the cancellation is recorded, and in force at some time on the cancelled day. Anything else is not a match: a cancellation under such a rule then becomes an operator ask, and the operator's answer is recorded on that cancellation (the bound policy version does not change). A value missing from the catalogue is added in code for later policies.
- **D3** No cancellation, no settlement: an ask still unanswered at finalize leaves the hold to be captured per the trip, and a cancellation recorded after finalize becomes an operator ask (refund after capture is out of scope). The assessor is not ported.

## Rationale

- C (Baseline 1) rather than Baseline 2 or B: every labelled clause pays when the operator cancels or accepts a cancellation; Pure Dive's and Kaia's official information is a condition on that decision, not a payout on its own. Settling on a warning alone would refund guests for a day the boat went out, and the operator, not an index, carries the basis risk the guest would see. Cost: the demo needs one operator click after a replayed warning, and the guest page shows "cancelled by the operator" with the warnings in force rather than a payout nobody touched.
- C rather than A: matching structured CAP fields is deterministic and reproducible from (policy version, evidence); a runtime model adds quota and failure modes on the path to `authorization_expires_at` and judges something code can read. Cost: we own a per-source event catalogue, and a renamed event waits for an operator confirmation instead of being understood on the spot.
- The AI stays where judgment is needed: reading wording, finding what it leaves unruled, mapping "storm warning" to a source's values, drafting asks. That is the demo's AI moment; settlement itself looks like a rules engine, a trade-off accepted for reproducible money.

## Expected Outcome

- Settlement code has no model call; replaying (policy version, rulings, cancellations with their recorded answers, operator approvals, evidence) reproduces every outcome.
- Step 2 measures compile quality (rules, needs-ruling precision and recall, event-set proposals), not runtime matching.
- A warning with no cancellation produces an ask, never money movement; the demo shows one operator click between the replayed warning and the void.

## Revocation Triggers

- A real operator wants settlement on warnings regardless of its own decision → B for that rule kind.
- Evidence arrives as free text (port notices, non-CAP feeds such as BoM RSS) → a runtime model proposing facts with citations, never amounts.
- Unmapped event values cause a missed or late settlement more than once → a runtime classifier for unknown values, operator-confirmed.

## Followup

- `AGENTS.md` product contract and Reuse, `requirements.md`, plan steps 2, 5, 8, 9, 11, `docs/threat-model.md`: changed with this ADR; the deposit/balance ADR's "daily evaluations" carries a review note.
- Step 5: confirming event sets in rulings; the trip's warning source must equal its bound policy's.
- Step 8: the cancellation action, D2 and D3 with tests; asks drafted with a template fallback and the daily cap.

## Related

- Plan: `docs/plans/2026-10-19-tidehold-hackathon.md` (D4, D12, steps 2, 5, 7, 8).
- Source assessor: `Will413028/dive-trip-agent@8065eba`, `backend/src/dive_trip/modules/deposits/assessor.py`.
- Decided by the developer on 2026-10-19 after a critiqued brief and two independent drafts; this ADR is the original record.
