#!/usr/bin/env bash
# Demonstrate that the ledger is append-only at the database level.
#
# This is a control an auditor can run themselves. It does not consult our
# application code at all -- it opens a direct SQL session and tries to rewrite
# history, first as the role the application actually uses, then as the table
# owner. Both must fail, for different reasons:
#
#   * the application role is denied by privilege (it was never granted UPDATE
#     or DELETE), which is what protects the ledger if the application is
#     compromised;
#   * the table owner is denied by trigger, which is what protects it from
#     anyone with a psql prompt and good intentions.
#
# Exit status is 0 only if every mutation attempt was refused.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGBIN="$ROOT/.pg/bin"
export PGHOST="${PGHOST:-$ROOT/.pgdata/sockets}"
export PGPORT="${PGPORT:-5433}"
DB_NAME="${DB_NAME:-swarmops_ledger}"

pass=0; fail=0
GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

# Run SQL and assert on the result.
#   expect_ok   <role> <label> <sql>   -- must succeed
#   expect_deny <role> <label> <sql>   -- must fail, and the error is shown
run_sql() {
  "$PGBIN/psql" -h "$PGHOST" -p "$PGPORT" -U "$1" -d "$DB_NAME" -X -q -t -A -c "$2" 2>&1
}

expect_ok() {
  local role="$1" label="$2" sql="$3" out
  out="$(run_sql "$role" "$sql")"
  if [ $? -eq 0 ]; then
    printf '  %sPASS%s  %-52s %sallowed, as intended%s\n' "$GREEN" "$OFF" "$label" "$DIM" "$OFF"
    pass=$((pass+1))
  else
    printf '  %sFAIL%s  %-52s %s\n' "$RED" "$OFF" "$label" "$out"
    fail=$((fail+1))
  fi
}

expect_deny() {
  local role="$1" label="$2" sql="$3" out
  out="$(run_sql "$role" "$sql")"
  if [ $? -ne 0 ]; then
    printf '  %sPASS%s  %-52s %s%s%s\n' "$GREEN" "$OFF" "$label" "$DIM" \
      "$(printf '%s' "$out" | grep -m1 -oE '(ERROR|FATAL):.*' | cut -c1-96)" "$OFF"
    pass=$((pass+1))
  else
    printf '  %sFAIL%s  %-52s %sSUCCEEDED -- the ledger is mutable!%s\n' "$RED" "$OFF" "$label" "$RED" "$OFF"
    fail=$((fail+1))
  fi
}

printf '\n%sAppend-only enforcement check%s  %s(database: %s)%s\n\n' "$BOLD" "$OFF" "$DIM" "$DB_NAME" "$OFF"

before="$(run_sql swarmops_app 'SELECT count(*) FROM agent_action_records;')"
printf '%s  as swarmops_app  -- the role the application runs as%s\n' "$BOLD" "$OFF"
expect_ok   swarmops_app "SELECT (read own ledger)"        'SELECT count(*) FROM agent_action_records;'
expect_deny swarmops_app "UPDATE a record"                 "UPDATE agent_action_records SET record = '{}'::jsonb;"
expect_deny swarmops_app "DELETE a record"                 'DELETE FROM agent_action_records;'
expect_deny swarmops_app "TRUNCATE the table"              'TRUNCATE agent_action_records;'
expect_deny swarmops_app "ALTER the table"                 'ALTER TABLE agent_action_records ADD COLUMN x int;'
expect_deny swarmops_app "DROP the table"                  'DROP TABLE agent_action_records;'
expect_deny swarmops_app "disable the tamper triggers"     'ALTER TABLE agent_action_records DISABLE TRIGGER ALL;'

printf '\n%s  as postgres  -- the table owner, who grants alone would not stop%s\n' "$BOLD" "$OFF"
expect_deny postgres "UPDATE a record"                     "UPDATE agent_action_records SET record = '{}'::jsonb;"
expect_deny postgres "DELETE a record"                     'DELETE FROM agent_action_records;'
expect_deny postgres "TRUNCATE the table"                  'TRUNCATE agent_action_records;'

after="$(run_sql swarmops_app 'SELECT count(*) FROM agent_action_records;')"
printf '\n%s  row count unchanged throughout: %s -> %s%s\n' "$DIM" "$before" "$after" "$OFF"

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
if [ "$fail" -ne 0 ] || [ "$before" != "$after" ]; then
  printf '%sAPPEND-ONLY ENFORCEMENT IS BROKEN%s\n\n' "$RED" "$OFF"; exit 1
fi
printf '%sLedger is append-only: every mutation attempt was refused by the database.%s\n\n' "$GREEN" "$OFF"
