# `make check` is the local gate, run by lefthook before every push with `make security`: every
# suite but the browser tests. `make ci` adds them and is run only by CI; nothing on the
# developer's machine runs `make e2e`.
.PHONY: check ci locks-local reports lint typecheck arch unit integration e2e locks coverage security dev-db dev-backend dev-web

WEB := pnpm --dir web
# Test reports read by `locks`; `check` starts from an empty directory so a suite that stops
# running shows up as missing instead of reusing an old report.
REPORTS := $(CURDIR)/build/test-reports
# unittest prints its summary to stderr: keep it for `locks`, show it, keep the exit status.
UNITTEST = python3 -m unittest -q $(1) 2> $(REPORTS)/$(2).txt; s=$$?; cat $(REPORTS)/$(2).txt >&2; exit $$s

check: reports lint typecheck arch unit integration locks-local

ci: reports lint typecheck arch unit integration e2e locks

reports:
	rm -rf $(REPORTS) && mkdir -p $(REPORTS)

lint:
	cd backend && uv run --frozen ruff format --check . ../scripts ../bench
	cd backend && uv run --frozen ruff check . ../recorder ../scripts ../bench
	$(WEB) lint

typecheck:
	cd backend && uv run --frozen mypy
	cd backend && uv run --frozen mypy ../bench
	$(WEB) typecheck

arch:
	cd backend && uv run --frozen lint-imports
	$(WEB) deps
	$(WEB) knip

unit:
	mkdir -p $(REPORTS)
	cd backend && uv run --frozen pytest -q tests/unit --junitxml=$(REPORTS)/backend-unit.xml
	cd backend && uv run --frozen pytest -q ../bench --junitxml=$(REPORTS)/bench.xml
	$(WEB) test --reporter=default --reporter=junit --outputFile.junit=$(REPORTS)/web.xml
	cd recorder && $(call UNITTEST,test_recorder,recorder)
	cd scripts && $(call UNITTEST,test_quality_lock test_deploy,scripts)

# Starts a throwaway PostgreSQL 18 container (testcontainers); needs Docker.
integration:
	mkdir -p $(REPORTS)
	cd backend && uv run --frozen pytest -q tests/integration --junitxml=$(REPORTS)/backend-integration.xml

# The demo path in Chromium at desktop width (Playwright): builds the web app and
# starts backend/tests/e2e/server.py (fakes for PayPal, the model and Open-Meteo, a throwaway
# PostgreSQL 18; needs Docker). The browser comes from Playwright's CDN, pinned by its version.
e2e:
	mkdir -p $(REPORTS)
	cd web && pnpm exec playwright install chromium
	cd web && pnpm exec playwright test
	cp web/test-results/junit.xml $(REPORTS)/web-e2e.xml

# Suppression comments may only go down and executed tests only up (quality-lock.json).
locks:
	python3 scripts/quality_lock.py check --reports $(REPORTS)

locks-local:
	python3 scripts/quality_lock.py check --reports $(REPORTS) --not-run web-e2e

# Report only, no threshold: line and branch coverage of the money path, read at each step's acceptance.
coverage:
	cd backend && uv run --frozen pytest -q tests \
		--cov=tidehold.money --cov=tidehold.holds --cov=tidehold.settlement --cov-branch \
		--cov-report=term-missing

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
	cd backend && DATABASE_URL=postgresql://postgres:dev@127.0.0.1:5433/tidehold PUBLIC_ORIGIN=http://localhost:3000 \
		uv run uvicorn tidehold.bootstrap.app:create_app --factory --port 8000

dev-web:
	$(WEB) dev
