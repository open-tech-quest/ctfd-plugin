FROM node:24.22.3-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS frontend
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
ARG PLUGIN_VERSION=dev
ENV VITE_PLUGIN_VERSION=${PLUGIN_VERSION}
RUN npm run check && npm run build \
    && test -s dist/quest.js && test -s dist/quest.css

FROM ctfd/ctfd:3.8.9@sha256:84aff1e50b6a6f931b5da90c6f5b7c623ba4583217a9b00e9d4c1b7c53b9739f AS application
ARG PLUGIN_VERSION=dev
ENV OTQ_PLUGIN_VERSION=${PLUGIN_VERSION}
LABEL org.opencontainers.image.source="https://github.com/open-tech-quest/ctfd-plugin" \
      org.opencontainers.image.title="Open Tech Quest" \
      org.opencontainers.image.licenses="MIT"
USER 1001
WORKDIR /opt/CTFd
COPY --chown=1001:1001 plugin/open_tech_quest/ CTFd/plugins/open_tech_quest/
COPY --chown=1001:1001 branding/ CTFd/plugins/open_tech_quest/assets/branding/
COPY --chown=1001:1001 --from=frontend /app/dist/ CTFd/plugins/open_tech_quest/assets/app/
RUN mkdir -p /opt/CTFd/data
# Keep the upstream entrypoint: core upgrades run before plugin load/migration.

FROM application AS test-runner
COPY --chown=1001:1001 tests/ /opt/quest-tests/

FROM test-runner AS test
# Test fixtures initialize disposable databases; this stage never ships.
RUN python -m unittest discover -s /opt/quest-tests -p 'test_*.py' -v

FROM application AS runtime
