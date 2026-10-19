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

The backend reads `DATABASE_URL` and `PUBLIC_ORIGIN` (the web origin; state-changing requests from any other origin are refused, and the session cookie is `Secure` when it is `https`). PayPal commands use sandbox credentials from `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`, loaded only when `PAYPAL_ENV=sandbox`; the client is pinned to `api-m.sandbox.paypal.com`. `python -m tidehold.bootstrap.sandbox` runs the sandbox smoke and the reauthorization check (see its docstring).

## Origin

The PayPal money engine (stored-before-sent commands with `PayPal-Request-Id`, reconciliation of unknown outcomes, the replace-or-send race guard) and the HTTP boundary are ported from the same developer's [dive-trip-agent](https://github.com/Will413028/dive-trip-agent) at commit `8065eba` (branch `feat/paypal-deposits`), its PayPal group-deposits demo, and re-evaluated for tidehold: payments instead of bookings and divers, CAPTURE orders for the deposit and reauthorization for the balance hold.
