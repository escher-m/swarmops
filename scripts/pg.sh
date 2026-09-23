#!/usr/bin/env bash
# Manage the project-local PostgreSQL cluster.
#
# Everything lives inside the repo: binaries in .pg/, data in .pgdata/, socket
# in .pgdata/sockets. Nothing is installed system-wide and no sudo is needed,
# so a reviewer can clone, run `scripts/pg.sh up`, and tear the whole thing down
# again with `rm -rf .pgdata`. Port 5433 avoids colliding with any Postgres the
# developer already runs on the default port.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PGBIN="$ROOT/.pg/bin"
export PGDATA="$ROOT/.pgdata"
export PGPORT="${PGPORT:-5433}"
export PGHOST="${PGHOST:-$PGDATA/sockets}"
DB_NAME="${DB_NAME:-swarmops_ledger}"

case "${1:-}" in
  init)
    if [ -d "$PGDATA/base" ]; then echo "cluster already initialised at $PGDATA"; exit 0; fi
    # initdb insists on an empty target, so the socket directory is created
    # afterwards rather than alongside it.
    mkdir -p "$PGDATA"
    "$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust \
        --encoding=UTF8 --locale=C --lc-messages=C >/dev/null
    mkdir -p "$PGDATA/sockets"
    echo "unix_socket_directories = '$PGDATA/sockets'" >> "$PGDATA/postgresql.conf"
    echo "port = $PGPORT"                              >> "$PGDATA/postgresql.conf"
    echo "listen_addresses = 'localhost'"              >> "$PGDATA/postgresql.conf"
    echo "initialised cluster at $PGDATA"
    ;;
  start)
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w start
    ;;
  stop)
    "$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop
    ;;
  status)
    "$PGBIN/pg_ctl" -D "$PGDATA" status || true
    ;;
  createdb)
    "$PGBIN/createdb" -h "$PGHOST" -p "$PGPORT" -U postgres "$DB_NAME" 2>/dev/null \
      && echo "created database $DB_NAME" || echo "database $DB_NAME already exists"
    ;;
  migrate)
    for f in "$ROOT"/ledger/sql/*.sql; do
      echo "applying $(basename "$f")"
      "$PGBIN/psql" -h "$PGHOST" -p "$PGPORT" -U postgres -d "$DB_NAME" -v ON_ERROR_STOP=1 -q -f "$f"
    done
    echo "migrations applied"
    ;;
  reset)
    # Development convenience only. This drops and recreates the whole database;
    # it is not a way to edit the ledger, and there is deliberately no command
    # that removes individual records.
    "$PGBIN/dropdb" -h "$PGHOST" -p "$PGPORT" -U postgres --if-exists "$DB_NAME"
    "$0" createdb && "$0" migrate
    ;;
  psql)
    shift
    exec "$PGBIN/psql" -h "$PGHOST" -p "$PGPORT" -U postgres -d "$DB_NAME" "$@"
    ;;
  up)
    "$0" init && "$0" start && "$0" createdb && "$0" migrate
    ;;
  *)
    echo "usage: $0 {init|start|stop|status|createdb|migrate|psql|up}" >&2
    exit 2
    ;;
esac
