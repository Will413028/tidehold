# tidehold API, migration, ingest and nightly-audit image (plan step 10a). Build from the
# repository root:
#   docker build -f deploy/backend.Dockerfile -t tidehold-backend:$(git rev-parse --short HEAD) .
# uv 0.12.23 with Python 3.13, the uv CI pins.
FROM ghcr.io/astral-sh/uv:0.13.6-python3.13-trixie-slim@sha256:e586c1c65b7071aec78f02c5587c76c5011159cd76b3d6f1255f40e48e850abc
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
