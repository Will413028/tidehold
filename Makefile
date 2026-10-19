# `make check` is the gate: lefthook runs it (with `make security`) before every push,
# and CI runs the same targets.
.PHONY: check lint typecheck arch unit integration security dev-db dev-backend dev-web

WEB := pnpm --dir web

check: lint typecheck arch unit integration

lint:
	cd backend && uv run --frozen ruff format --check . && uv run --frozen ruff check .
	$(WEB) lint

typecheck:
	cd backend && uv run --frozen mypy
	$(WEB) typecheck

arch:
	cd backend && uv run --frozen lint-imports
	$(WEB) deps
	$(WEB) knip

unit:
	cd backend && uv run --frozen pytest -q tests/unit
	$(WEB) test
	cd recorder && python3 -m unittest -q test_recorder

# Starts a throwaway PostgreSQL 18 container (testcontainers); needs Docker.
integration:
	cd backend && uv run --frozen pytest -q tests/integration

# Lockfiles are listed: a new one must be added here and in .github/workflows.
security:
	gitleaks git --redact --no-banner .
	osv-scanner scan source -L backend/uv.lock -L web/pnpm-lock.yaml
	actionlint
	zizmor --offline .github/workflows

dev-db:
	docker run --rm -d --name tidehold-dev-db -p 127.0.0.1:5433:5432 \
		-e POSTGRES_PASSWORD=dev -e POSTGRES_DB=tidehold postgres:18-alpine

dev-backend:
	cd backend && DATABASE_URL=postgresql://postgres:dev@127.0.0.1:5433/tidehold uv run python -m tidehold.platform.migrate
	cd backend && DATABASE_URL=postgresql://postgres:dev@127.0.0.1:5433/tidehold COOKIE_SECURE=false \
		uv run uvicorn tidehold.bootstrap.app:create_app --factory --port 8000

dev-web:
	$(WEB) dev
