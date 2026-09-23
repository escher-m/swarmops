# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the React islands
#
# Node exists only here. The runtime image has no npm, no node_modules and no
# JavaScript toolchain; it gets two built files. A compliance dashboard should
# not carry a package manager into production.
# ─────────────────────────────────────────────────────────────────────────────
FROM node:22-slim AS islands

# The repo layout is mirrored rather than flattened: vite.config.js writes to
# `../static/islands` relative to itself, so the build has to sit at the same
# depth or the output lands somewhere surprising.
WORKDIR /src/dashboard/ui
COPY dashboard/ui/package.json dashboard/ui/package-lock.json ./
# `npm ci` installs exactly the lockfile and fails if the two disagree, which
# is the behaviour you want in a build -- unlike `npm install`.
RUN npm ci --no-audit --no-fund

COPY dashboard/ui/ ./
RUN npm run build && test -f /src/dashboard/static/islands/islands.js


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — python dependencies
#
# Separated so that changing application code does not re-resolve and re-build
# every wheel. Installed into a virtualenv that is copied wholesale into the
# final stage, which keeps build toolchains out of the runtime image.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY deploy/requirements-dashboard.txt /tmp/
RUN pip install -r /tmp/requirements-dashboard.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# postgresql-client supplies psql, which the entrypoint uses to apply the
# migrations. It is the only OS package added.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Runs unprivileged. The ledger's whole premise is that the application cannot
# rewrite history; running it as root inside its own container would not break
# that (the database enforces it) but it is the wrong default to ship.
RUN useradd --system --uid 10001 --create-home --home-dir /app --shell /usr/sbin/nologin swarmops
WORKDIR /app

COPY --from=deps /opt/venv /opt/venv

# Application code. Only what the dashboard actually imports -- the adapter,
# the seeded service and the trial harness are not part of this image.
COPY --chown=swarmops:swarmops ledger/    ./ledger/
COPY --chown=swarmops:swarmops approval/  ./approval/
COPY --chown=swarmops:swarmops dashboard/ ./dashboard/
COPY --chown=swarmops:swarmops schema/    ./schema/
COPY --chown=swarmops:swarmops policy/    ./policy/
COPY --chown=swarmops:swarmops scripts/verify_chain.py scripts/__init__.py ./scripts/

# The islands bundle, built in stage 1.
COPY --from=islands --chown=swarmops:swarmops /src/dashboard/static/islands ./dashboard/static/islands

COPY --chown=swarmops:swarmops deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Evidence blobs live here and MUST be a persistent volume. Without one, the
# store is empty on every restart and verify_chain.py correctly starts
# reporting evidence_missing on records that were never touched.
RUN mkdir -p /var/lib/swarmops/evidence && chown -R swarmops:swarmops /var/lib/swarmops
VOLUME ["/var/lib/swarmops/evidence"]
ENV SWARMOPS_EVIDENCE_DIR=/var/lib/swarmops/evidence

USER swarmops
EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers", "--forwarded-allow-ips", "*"]
