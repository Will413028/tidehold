# tidehold

Weather-cancellation deposits for dive operators, built for the PayPal AI Hackathon.

An AI agent turns a dive operator's own cancellation wording into structured rules, each citing the clause it came from. The guest's trip balance is held as a PayPal authorization, not a charge. When the national weather service the operator names issues an official warning, the agent voids or partially captures each hold within the operator's mandate, and stops to ask the operator whenever the policy does not decide the case. Guests see the official warning and the clause behind their outcome.

PayPal sandbox and synthetic guests only; no real money moves. Whether a boat goes out is always the operator's call; this is not a dive-safety tool.

Work in progress.

## Develop

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22+ with pnpm, Docker and [lefthook](https://lefthook.dev/).

```sh
uv sync --project backend && pnpm --dir web install && lefthook install
make check          # lint, typecheck, layer checks, unit and integration tests
make dev-db         # PostgreSQL 18 on 127.0.0.1:5433
make dev-backend    # migrate, then the API on :8000
make dev-web        # Next.js on :3000, proxying /api to the backend
```
