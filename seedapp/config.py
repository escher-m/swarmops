"""Runtime configuration for the seeded checkout service.

Config lives in a JSON file rather than environment variables so that a fault
injection or a remediation is a visible, inspectable state change that both the
agent and a human can read. `state.json` is effectively this service's
deployment manifest.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
STATE_PATH = APP_DIR / "state.json"
LOG_DIR = APP_DIR / "logs"
LOG_PATH = LOG_DIR / "checkout-api.log"
REPO_DIR = APP_DIR / "repo"

# The healthy baseline. `scripts/seed_reset.py` and POST /admin/reset both
# return the service to exactly this.
DEFAULTS: dict[str, Any] = {
    # Which release is deployed. v1.4.2 contains the null-shipping_cost bug.
    "version": "v1.4.1",
    # Max simultaneous database connections the service will hold.
    "db_pool_max": 10,
    # Whether the functional index supporting revenue lookups exists.
    "revenue_index_enabled": True,
    # Whether /reports/revenue issues one query per customer (the N+1 bug).
    "revenue_n_plus_one": False,
    # Credentials the service believes are live.
    "active_credential_id": "AKIA2F7QXVEXAMPLE001",
    "faults": [],
}

_lock = threading.Lock()


def load() -> dict[str, Any]:
    with _lock:
        if not STATE_PATH.exists():
            STATE_PATH.write_text(json.dumps(DEFAULTS, indent=2))
            return dict(DEFAULTS)
        state = json.loads(STATE_PATH.read_text())
        # Tolerate a state file written by an older build.
        return {**DEFAULTS, **state}


def save(state: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        STATE_PATH.write_text(json.dumps(state, indent=2))
    return state


def update(**changes: Any) -> dict[str, Any]:
    state = load()
    state.update(changes)
    return save(state)


def reset() -> dict[str, Any]:
    return save(dict(DEFAULTS))
