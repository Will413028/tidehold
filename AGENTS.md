# tidehold

Weather-cancellation deposits for dive operators, built for the PayPal AI Hackathon on Devpost. Empty repository: no runnable scaffold, commands or stack yet.

## Product contract (decided)

- Primary user: a weather-dependent dive operator anywhere that takes trip payments from guests. The guest sees one transparency page; there is no guest product of its own.
- The agent compiles the operator's own cancellation wording into structured rules. Every rule carries a verbatim span from that wording; a clause that is silent or left to the operator's discretion becomes "needs operator ruling", never a guess.
- The trip balance is held as a PayPal Orders v2 AUTHORIZE about 28 days before the trip, so the authorization stays inside PayPal's 29-day validity (reauthorization cannot extend past day 29). The booking deposit stays as the operator handles it today.
- Triggers are official warnings in CAP (Common Alerting Protocol) from the national weather service the operator names in its policy (e.g. PAGASA, NWS, MeteoAlarm, JMA, BoM, CWA). Open-Meteo marine forecasts only corroborate; Open-Meteo states it is not for coastal navigation.
- The model never supplies amounts. Code computes every amount from the compiled rules. Inside the operator's mandate the agent voids or partially captures holds; outside it, or on any unruled clause, it stops and asks the operator.
- Credit (as opposed to refund) is an internal ledger record; PayPal has no native voucher.
- PayPal sandbox and synthetic guests only; no real money. Whether a boat goes out is always the operator's call; this is not a dive-safety tool.
- Real operators' clauses may be quoted with their source URL and access date; the demo operator is a neutral fictional name with no real operator's branding.
- Recorded official feeds used in a demo are labelled as recorded, with their fetch time; the agent sees only data timestamped before each decision point.

## Reuse

The money-command engine (stored-before-sent PayPal commands with PayPal-Request-Id, reconciliation, mandate check, server-verified citations, Temporal workflow) comes from the public dive-trip-agent repository's deposits module (`backend/src/dive_trip/modules/deposits/`, `docs/paypal-group-deposits.md`). Re-evaluate each mechanism when porting instead of copying it wholesale.
