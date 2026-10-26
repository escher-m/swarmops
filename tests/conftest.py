import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOCKET = ROOT / ".pgdata" / "sockets"
ADMIN_DSN = f"host={SOCKET} port=5433 user=postgres dbname=postgres"


# Database fixtures shared by every test that needs a real ledger. They use a
# throwaway database per session rather than the demo ledger, because the
# ledger is append-only by design: a test that wrote into it could never clean
# up after itself.


@pytest.fixture(scope="session")
def test_db():
    if not (ROOT / ".pgdata" / "postmaster.pid").exists():
        pytest.skip("local PostgreSQL cluster is not running (scripts/pg.sh start)")
    import psycopg

    name = f"swarmops_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    dsn = f"host={SOCKET} port=5433 user=postgres dbname={name}"
    with psycopg.connect(dsn) as conn:
        for sql in sorted((ROOT / "ledger" / "sql").glob("*.sql")):
            conn.execute(sql.read_text())
        conn.commit()
    yield dsn
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def store(test_db):
    from ledger.store import LedgerStore

    return LedgerStore(test_db)


@pytest.fixture
def recorder(store):
    from ledger.recorder import Actor, Recorder, Target

    return Recorder(
        incident_id=f"INC-{uuid.uuid4().hex[:6]}",
        actor=Actor("test-agent", "0.0.1", "test", "inv-1"),
        target=Target("k8s_deployment", "staging/api", "staging"),
        store=store,
    )
