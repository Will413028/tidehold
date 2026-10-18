# tidehold

Weather-cancellation deposits for dive operators, built for the PayPal AI Hackathon on Devpost. Empty repository: no runnable scaffold, commands or stack yet.

## Product contract (decided)

- Primary user: a weather-dependent dive operator anywhere that takes trip payments from guests. The guest sees one transparency page; there is no guest product of its own.
- The agent compiles the operator's own cancellation wording into structured rules. Every rule carries a verbatim span from that wording; a clause that is silent or left to the operator's discretion becomes "needs operator ruling", never a guess.
- The booking deposit is captured through PayPal at booking; on a weather cancellation it is refunded or recorded as credit, per the compiled policy.
- The trip balance is held as a PayPal Orders v2 AUTHORIZE at T-(29 − N) for an N-day trip, so every capture falls inside PayPal's 29-day validity; a capture after the 3-day honor period is preceded by a reauthorization; trips longer than 10 days are refused. See `docs/adr/2026-10-19-deposit-capture-and-balance-hold.md`.
- Triggers are official warnings in CAP (Common Alerting Protocol) from the national weather service the operator names in its policy (e.g. PAGASA, NWS, MeteoAlarm, JMA, BoM, CWA). Open-Meteo marine forecasts only corroborate; Open-Meteo states it is not for coastal navigation.
- The model never supplies amounts. Code computes every amount from the compiled rules. Inside the operator's mandate the agent voids or partially captures holds; outside it, or on any unruled clause, it stops and asks the operator.
- Credit (as opposed to refund) is an internal ledger record; PayPal has no native voucher.
- PayPal sandbox and synthetic guests only; no real money. Whether a boat goes out is always the operator's call; this is not a dive-safety tool.
- Real operators' clauses may be quoted with their source URL and access date; the demo operator is a neutral fictional name with no real operator's branding.
- Recorded official feeds used in a demo are labelled as recorded, with their fetch time; the agent sees only data timestamped before each decision point.

## Reuse

The money-command engine (stored-before-sent PayPal commands with PayPal-Request-Id, reconciliation, mandate check, server-verified citations) comes from `Will413028/dive-trip-agent` at commit `8065eba` (branch `feat/paypal-deposits`): `backend/src/dive_trip/modules/deposits/` and its dependencies, as listed in the plan's step 4. Re-evaluate each mechanism when porting instead of copying it wholesale. Time-based work runs on a PostgreSQL `due_actions` table, not a workflow engine (`docs/adr/2026-10-19-runtime-without-workflow-engine.md`).
