# tidehold requirements, interfaces and risks

Sources (numbered): S1 product contract `AGENTS.md`; S2 concept critique (2026-10-19; its decided parts are the plan's DA and the AGENTS.md contract); S3 `research-operators.md`; S4 `research-ideation.md`; S5 PayPal AI Hackathon rules and judging criteria (https://paypalaihackathon.devpost.com/rules, read 2026-10-18).

## Requirements

Every item maps to a plan step (column "Step"); `scripts/check_plan.py` in this directory fails if one is missing.

| ID | Kind | Requirement | Source | Step |
|---|---|---|---|---|
| R1 | Flow | Operator pastes its own cancellation wording; the agent compiles it into structured rules, each with a verbatim span that the server verifies against the stored wording | S1, S2 | 5 |
| R2 | Flow | A clause that is silent or left to the operator's discretion is flagged "needs operator ruling"; the operator records a ruling; no rule is executed while unruled | S1, S2 | 5, 8 |
| R3 | Data | A compiled policy is an immutable version; trips bind to one version | inferred from S1 (amounts must be reproducible) | 5 |
| R4 | Flow | Operator creates a trip (site coordinates, warning area, dates, balance per guest) and invites guests | S1 | 6 |
| R5 | Flow | Guest pays the trip balance as a PayPal Orders v2 AUTHORIZE through PayPal checkout, requested about 28 days before the trip (immediately when the trip is closer) | S1, S2 | 6 |
| R6 | Flow | Official CAP warnings from the source the operator's policy names are fetched, stored with fetch time, and matched to the trip's area | S1, S2 | 7 |
| R7 | Flow | Open-Meteo marine forecast at the site corroborates; it never triggers alone | S1, S2 | 7 |
| R8 | Flow | Replay mode: a recorded feed set is served with its original fetch times; the agent sees only data timestamped before the decision time | S1, S2 | 7 |
| R9 | Flow | Before each trip day the system evaluates the bound policy against the evidence; code computes each guest's outcome (void, partial capture with final_capture, capture, credit) | S1 | 8 |
| R10 | Flow | Inside the operator's mandate the agent executes; outside it, or on an unruled clause, it stops and asks; the operator approves or rejects | S1 | 8 |
| R11 | Data | Credit is an internal ledger record per guest, not a PayPal object | S1, S2 | 8 |
| R12 | Screen | Guest transparency page: hold status, the official bulletin, the clause, the outcome | S1, S2 | 9 |
| R13 | Screen | Operator console: policy compile and rulings, trips, guests, evidence, proposals, audit timeline | S1, S5 Design | 9 |
| R14 | NFR | Every PayPal write is stored before it is sent with PayPal-Request-Id; unknown outcomes reconcile; holds stay within 29 days | S1 | 4, 6 |
| R15 | NFR | Sandbox only, synthetic guests; real operators' clauses only as attributed quotes; neutral fictional demo operator | S1 | 4, 10 |
| R16 | Ops | Public demo URL judges can use without accounts; daily void-all and rebuild | S5 | 10 |
| R17 | Ops | Model provider chosen from a measured benchmark on free tiers | S2, session decision 2026-10-18 | 2, 11 |
| R18 | Deliverable | Devpost content, README, screenshots, architecture diagram, impact text with sources, <3 min video in the developer's voice | S5 | 12, 13 |

## Non-functional requirements

| Area | Threshold |
|---|---|
| Latency | Policy compile p95 ≤ 60 s for ≤ 4,000 characters (measured in step 5); settlement evaluation p95 ≤ 60 s per trip day (measured in step 8) |
| Cost | Free model tiers until judging (paid tier decided later, see D6); PayPal sandbox free; hosting on the existing VM |
| Security | Server-only provider keys; no secrets in the repo; guest pages reachable only by unguessable link; anonymous operator workspace per browser (reused pattern) |
| Privacy | Synthetic guests only; no real personal data entered or stored |
| Observability | Audit timeline per trip covers every evidence fetch, decision and PayPal call |
| Retention | Demo DB rebuilt daily after voiding open holds; recorded feeds kept in the repo or object storage with source and fetch time |
| Availability | Best effort; recorded fallback video covers outages |

## External interfaces

| Interface | Use | Limits and terms | Source (read date) |
|---|---|---|---|
| PayPal Orders v2 AUTHORIZE, Authorizations capture/void, Captures refund, JS SDK buttons | Balance holds and settlement | Authorization valid 29 days, honor period 3 days; reauthorize days 4–29 only; capture up to 115% or +USD 75 | https://developer.paypal.com/docs/checkout/standard/customize/authorization/ , https://developer.paypal.com/docs/api/payments/v2/authorizations-reauthorize (2026-10-19, via critique) |
| PayPal Vault | Not used | Not available to PH/ID/TH/MY merchants | https://developer.paypal.com/docs/checkout/save-payment-methods/during-purchase/js-sdk/cards/ (2026-10-19, via critique) |
| PAGASA CAP / Atom | Official warnings (PH) | CC BY 4.0; reachable 2026-10-18 | https://publicalert.pagasa.dost.gov.ph/feeds/ |
| NWS alerts API (US) | Official warnings (US) | To verify in step 1 | https://api.weather.gov/alerts |
| MeteoAlarm (EU) | Official warnings (EU) | To verify in step 1 | https://feeds.meteoalarm.org |
| JMA XML feed | Official warnings (JP) | Official XML feed; bosai JSON is undocumented and not used | https://www.data.jma.go.jp/developer/xml/feed/extra.xml |
| BoM, BMKG, CWA | Official warnings (AU, ID, TW) | To verify in step 1; CWA open data needs an API key | — |
| Open-Meteo Marine | Corroborating wave forecast | Free non-commercial, 10,000 calls/day, CC BY 4.0, "not suitable for coastal navigation" | https://marine-api.open-meteo.com/v1/marine (2026-10-18) |
| Groq, Gemini APIs | Agent models | Free tiers; models available to the existing keys listed 2026-10-18 | session check 2026-10-18 |

## Precedents

- dive-trip-agent deposits demo (same developer, public): proves AUTHORIZE holds, void, partial final capture, refund, reconciliation and mandate gating work in the PayPal sandbox; does not prove policy compilation or official-warning triggers.
- Airline auto-rebooking, Hopper/Kayak guarantees, travel insurers: prove demand for automated disruption handling on the traveller side; none handles operator policy wording or authorization holds.

## Product risks

| Risk | Evidence today |
|---|---|
| Do operators want it? | 5 of 15 SEA operators state PayPal deposits on their own pages; weather terms are inconsistent; several key off official warnings (S3). No operator interview. |
| Will they reuse it? | Peak typhoon season Jul–Oct in PH (~20 cyclones/yr enter PAR); in-season cancellation rate not found (S3). |
| Will they pay? | Unknown; PayPal fees (3–4.5%) are already passed to guests (S3). |
