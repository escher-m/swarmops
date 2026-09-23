# Convenience targets. Every one of these is a plain script under scripts/;
# nothing here is required to use the project.
VENV := .venv/bin

.PHONY: setup db-up db-down seed app dashboard test verify tamper append-only incident trials site site-serve islands shots clean

setup:        ; bash scripts/setup.sh
db-up:        ; ./scripts/pg.sh up
db-down:      ; ./scripts/pg.sh stop
seed:         ; $(VENV)/python scripts/seed_db.py && bash scripts/seed_repo.sh
app:          ; $(VENV)/python -m seedapp.app
dashboard:    ; $(VENV)/python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8090 --reload
test:         ; $(VENV)/python -m pytest tests/ -q
verify:       ; $(VENV)/python scripts/verify_chain.py
tamper:       ; $(VENV)/python scripts/tamper_demo.py
append-only:  ; bash scripts/prove_append_only.sh
incident:     ; $(VENV)/python scripts/run_incident.py --fault $(FAULT)
trials:       ; $(VENV)/python scripts/run_trials.py --runs $(or $(RUNS),20)
islands:      ; cd dashboard/ui && npm install && npm run build
shots:        ; $(VENV)/python scripts/capture_shots.py
site:         ; $(VENV)/python site/build.py
site-serve:   ; $(VENV)/python site/build.py --serve
clean:        ; ./scripts/pg.sh stop || true; rm -rf .pgdata evidence_store/* runs/*
