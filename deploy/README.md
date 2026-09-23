# Deploying the dashboard

The dashboard is not a hosted service. It runs inside the estate that produces
the agent actions it records, because the product's whole claim is that traces
and evidence never leave that estate. This directory is the reference
deployment a customer runs.

```bash
cp deploy/.env.example deploy/.env       # fill in the secrets
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

Run it from the repository root: the build context is the repo, so the image
can copy application code.

## What you get

Two containers. `postgres` holds the ledger on a named volume; `dashboard`
serves on 8090, bound to loopback by default.

The dashboard image carries twelve Python packages, not the 190 in the root
`requirements.txt`. HolmesGPT, litellm, the Kubernetes client and boto3 are all
absent, because a service whose job is to read a database and render HTML has
no use for them and every CVE in the LLM stack would otherwise become a CVE in
your compliance dashboard.

## Secrets

Three are mandatory and the container refuses to start without them:

| Variable | What it is |
|---|---|
| `SWARMOPS_DB_SUPERUSER_PASSWORD` | Used only by the entrypoint, to run migrations and set the app role's password. The application never sees it. |
| `SWARMOPS_APP_PASSWORD` | The restricted role the application connects as. Holds `INSERT` and `SELECT`, nothing else. |
| `SWARMOPS_SECRET_KEY` | Signs session cookies. `openssl rand -base64 48`. |

The migrations create the application role **without** a password on purpose,
so no credential is committed to the repository. The entrypoint sets it from
the environment on every boot, which also means rotating it is a restart rather
than a migration.

`SWARMOPS_ENV=production` is set in the compose file. That makes the dashboard
refuse to start with development identities or with authentication disabled, so
OIDC settings are not optional here.

## The evidence volume matters

`evidence:/var/lib/swarmops/evidence` is not incidental. Evidence blobs are
content-addressed and referenced by records that outlive any container. Without
a persistent volume the store is empty after every restart and
`verify_chain.py` correctly reports `evidence_missing` on records nobody has
touched — an integrity alarm caused purely by deployment. Back it up with the
database, not separately: a ledger whose evidence has been restored to a
different point in time will not verify.

## Before this faces anything real

* **Put TLS in front.** The port is bound to loopback by default, and session
  cookies are marked `Secure` only when the request arrived over HTTPS. Widen
  `SWARMOPS_BIND` only once a terminating proxy exists.
* **Use secrets, not environment variables.** The compose file reads a `.env`
  because that is the shape everyone recognises. Docker secrets, Kubernetes
  secrets or a secret manager are better; the entrypoint reads plain
  environment variables, so a file-based secret needs exporting first.
* **Back up and drill a restore.** Append-only means damage cannot be repaired
  in place. There is no backup story in this directory yet; see `TODO.md`.

## What has been verified, and what has not

Verified against a real PostgreSQL 16:

* the migrations apply cleanly to an empty database
* the entrypoint's `ALTER ROLE` sets a password containing quotes, `$` and
  spaces without breaking — psql's `:'var'` escaping handles it
* the app role can `SELECT` and still cannot `DELETE` afterwards
* every `COPY` source exists and is not excluded by `.dockerignore`
* the pinned runtime dependencies resolve

* every pinned dependency resolves to a prebuilt wheel for **both**
  `linux/amd64` and `linux/arm64`, with no source builds — which matters,
  because the slim base image has no compiler
* the file set the Dockerfile copies is import-complete: `dashboard.app` and
  everything it reaches import successfully in a tree containing only those
  directories and only these twelve packages

**Not verified: `docker build` and `docker compose up` have never been run.**
There is no container runtime on the machine this was written on. What remains
untested is mostly base-image behaviour: whether `npm ci` is happy with the
committed lockfile on node:22-slim, and whether `read_only: true` holds — the
assumption is that nothing writes outside `/tmp` and the evidence volume, which
is true as far as the code goes but has not been exercised. Build it once
somewhere with Docker before trusting it.
