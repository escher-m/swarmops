#!/usr/bin/env bash
# Container entrypoint for the dashboard.
#
# Three jobs before handing off to the server: wait for the database, apply the
# migrations, and give the application role a password. Then exec, so uvicorn
# becomes PID 1 and receives SIGTERM directly rather than through a shell that
# would swallow it and force the orchestrator to SIGKILL.
set -euo pipefail

: "${SWARMOPS_DB_HOST:=postgres}"
: "${SWARMOPS_DB_PORT:=5432}"
: "${SWARMOPS_DB_NAME:=swarmops_ledger}"
: "${SWARMOPS_DB_SUPERUSER:=postgres}"
: "${SWARMOPS_APP_USER:=swarmops_app}"

need() {
  if [ -z "${!1:-}" ]; then
    echo "entrypoint: $1 is required but not set." >&2
    exit 78   # EX_CONFIG
  fi
}

# Refuse to start half-configured rather than failing later in a way that looks
# like a database problem.
need SWARMOPS_DB_SUPERUSER_PASSWORD
need SWARMOPS_APP_PASSWORD
need SWARMOPS_SECRET_KEY

export PGHOST="$SWARMOPS_DB_HOST" PGPORT="$SWARMOPS_DB_PORT"

echo "entrypoint: waiting for postgres at $PGHOST:$PGPORT"
for i in $(seq 1 60); do
  if PGPASSWORD="$SWARMOPS_DB_SUPERUSER_PASSWORD" \
     psql -U "$SWARMOPS_DB_SUPERUSER" -d postgres -c 'SELECT 1' >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "entrypoint: postgres did not become ready within 60s" >&2
    exit 75   # EX_TEMPFAIL — orchestrators should retry this
  fi
  sleep 1
done

export PGPASSWORD="$SWARMOPS_DB_SUPERUSER_PASSWORD"

# Create the database if this is a first boot. `createdb` would fail on the
# second, so ask first.
if ! psql -U "$SWARMOPS_DB_SUPERUSER" -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='$SWARMOPS_DB_NAME'" | grep -q 1; then
  echo "entrypoint: creating database $SWARMOPS_DB_NAME"
  psql -U "$SWARMOPS_DB_SUPERUSER" -d postgres -c "CREATE DATABASE \"$SWARMOPS_DB_NAME\""
fi

# Migrations are idempotent (CREATE ... IF NOT EXISTS, CREATE OR REPLACE), so
# running them on every boot is safe and keeps a redeploy self-healing.
echo "entrypoint: applying migrations"
for f in /app/ledger/sql/*.sql; do
  psql -U "$SWARMOPS_DB_SUPERUSER" -d "$SWARMOPS_DB_NAME" -v ON_ERROR_STOP=1 -q -f "$f"
done

# The migrations deliberately create the application role WITHOUT a password,
# so none is committed to the repository. Over a TCP connection Postgres needs
# one, so it is set here from the environment on every boot -- which also means
# rotating it is a restart, not a migration.
echo "entrypoint: setting password for $SWARMOPS_APP_USER"
# Piped on stdin rather than passed with -c: psql performs variable
# interpolation for input read from a file or stdin, but NOT for -c, where the
# string goes to the server verbatim and `:'app_password'` is a syntax error.
#
# The interpolation forms matter. :"name" quotes an identifier and :'name'
# quotes a literal with proper escaping, so a password containing a single
# quote cannot terminate the string early. Building this by string
# concatenation in bash would be an injection waiting to happen.
printf 'ALTER ROLE :"app_user" LOGIN PASSWORD :%s;\n' "'app_password'" \
  | psql -U "$SWARMOPS_DB_SUPERUSER" -d "$SWARMOPS_DB_NAME" -v ON_ERROR_STOP=1 -q \
      -v app_user="$SWARMOPS_APP_USER" -v app_password="$SWARMOPS_APP_PASSWORD"

unset PGPASSWORD

# The application connects as the restricted role, which holds only INSERT and
# SELECT. It never sees the superuser credential.
export SWARMOPS_LEDGER_DSN="host=$SWARMOPS_DB_HOST port=$SWARMOPS_DB_PORT \
dbname=$SWARMOPS_DB_NAME user=$SWARMOPS_APP_USER password=$SWARMOPS_APP_PASSWORD"

echo "entrypoint: starting $*"
exec "$@"
