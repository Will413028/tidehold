# tidehold web image (Next production server, plan step 10a). Build from the repository root:
#   docker build -f deploy/web.Dockerfile -t tidehold-web:$(git rev-parse --short HEAD) .
# Node 24.21.0, the major CI runs.
FROM node:26.12.1-trixie-slim@sha256:94816674c62774da0d099a947bf4613b9e1972a2733abd0d0cd80e795f8411d0
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
# corepack (bundled with Node 24) installs the pnpm that package.json's packageManager names.
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY web/ ./
# The /api rewrite is compiled into the routes manifest at build time: web and the API share
# one network namespace, and the API listens on its loopback.
RUN BACKEND_URL=http://127.0.0.1:8000 pnpm build && chown -R node:node .next
USER node
CMD ["node", "node_modules/next/dist/bin/next", "start", "-H", "0.0.0.0", "-p", "3000"]
