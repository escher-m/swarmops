#!/usr/bin/env bash
# One-command bootstrap. Everything lands inside this directory; nothing is
# installed system-wide and no sudo is required.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

say "1/7  Python environment"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

say "2/7  PostgreSQL 16 (conda-forge build, kept in .pg/)"
if [ ! -x .pg/bin/postgres ]; then
  mkdir -p .tools
  if [ ! -x .tools/bin/micromamba ]; then
    curl -fsSL "https://micro.mamba.pm/api/micromamba/$(uname -s | tr 'A-Z' 'a-z')-$(uname -m | sed 's/x86_64/64/;s/arm64/arm64/')/latest" \
      | tar -xj -C .tools bin/micromamba
    chmod +x .tools/bin/micromamba
  fi
  MAMBA_ROOT_PREFIX="$ROOT/.tools/mamba" ./.tools/bin/micromamba create -y -q -p "$ROOT/.pg" -c conda-forge "postgresql=16"
fi
.pg/bin/postgres --version

say "3/7  Ledger database"
./scripts/pg.sh up

say "4/7  Seeded service database and repository"
.venv/bin/python scripts/seed_db.py
bash scripts/seed_repo.sh

say "5/7  Schema fixtures"
.venv/bin/python scripts/make_fixtures.py

say "6/7  Dashboard React islands"
if command -v npm >/dev/null 2>&1; then
  (cd dashboard/ui && npm install --silent && npm run build)
else
  echo "npm not found - skipping. The dashboard still works; the command palette"
  echo "simply will not mount, and every destination it offers is in the sidebar."
fi

say "7/7  Tests"
.venv/bin/python -m pytest tests/ -q

cat <<'EOF'

Setup complete.

  Start the seeded service:   .venv/bin/python -m seedapp.app
  Start the dashboard:        .venv/bin/python -m uvicorn dashboard.app:app --port 8090
  Prove append-only:          bash scripts/prove_append_only.sh
  Prove tamper detection:     .venv/bin/python scripts/tamper_demo.py
  Verify the chain:           .venv/bin/python scripts/verify_chain.py

To run a real investigation you need an LLM key:

  cp .env.example .env    # then set ANTHROPIC_API_KEY
  .venv/bin/python scripts/run_incident.py --fault bad_deploy

EOF
