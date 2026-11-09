# tidehold API, migration, ingest and nightly-audit image (plan step 10a). Build from the
# repository root:
#   docker build -f deploy/backend.Dockerfile -t tidehold-backend:$(git rev-parse --short HEAD) .
# uv 0.12.23 with Python 3.13, the uv CI pins.
FROM ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie-slim@sha256:6ff812e34a0e14b4b8bc9ddf25e77b3e16bac6e0c4d9b67641f40366fa382d4c
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src src
# The reconciliation log directory is owned by the app user, so a new named volume mounted
# there inherits that ownership and is writable.
RUN uv sync --frozen --no-dev \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin tidehold \
 && mkdir -p /var/log/tidehold \
 && chown 10001 /var/log/tidehold
USER 10001
ENTRYPOINT ["/app/.venv/bin/python", "-m"]
