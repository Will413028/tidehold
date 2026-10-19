# `make check` is the gate: lefthook runs it (with `make security`) before every push,
# and CI runs the same targets.
.PHONY: check reports lint typecheck arch unit integration locks security dev-db dev-backend dev-web

WEB := pnpm --dir web
# Test reports read by `locks`; `check` starts from an empty directory so a suite that stops
# running shows up as missing instead of reusing an old report.
REPORTS := $(CURDIR)/build/test-reports
# unittest prints its summary to stderr: keep it for `locks`, show it, keep the exit status.
UNITTEST = python3 -m unittest -q $(1) 2> $(REPORTS)/$(2).txt; s=$$?; cat $(REPORTS)/$(2).txt >&2; exit $$s

check: reports lint typecheck arch unit integration locks

reports:
	rm -rf $(REPORTS) && mkdir -p $(REPORTS)

lint:
	cd backend && uv run --frozen ruff format --check . ../scripts
	cd backend && uv run --frozen ruff check . ../recorder ../scripts
	$(WEB) lint

typecheck:
	cd backend && uv run --frozen mypy
	$(WEB) typecheck

arch:
	cd backend && uv run --frozen lint-imports
	$(WEB) deps
	$(WEB) knip

unit:
	mkdir -p $(REPORTS)
	cd backend && uv run --frozen pytest -q tests/unit --junitxml=$(REPORTS)/backend-unit.xml
	$(WEB) test --reporter=default --reporter=junit --outputFile.junit=$(REPORTS)/web.xml
	cd recorder && $(call UNITTEST,test_recorder,recorder)
	cd scripts && $(call UNITTEST,test_quality_lock,scripts)

# Starts a throwaway PostgreSQL 18 container (testcontainers); needs Docker.
integration:
	mkdir -p $(REPORTS)
	cd backend && uv run --frozen pytest -q tests/integration --junitxml=$(REPORTS)/backend-integration.xml

# Suppression comments may only go down and executed tests only up (quality-lock.json).
locks:
	python3 scripts/quality_lock.py check --reports $(REPORTS)

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
