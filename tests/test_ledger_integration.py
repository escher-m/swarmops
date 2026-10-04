"""Integration tests against a real PostgreSQL ledger.

These use a throwaway database per session rather than the demo ledger, because
the ledger is append-only by design: a test that wrote into it could never
clean up after itself, and the demo data would accumulate test noise forever.
"""

import json
import uuid
from pathlib import Path

import psycopg
import pytest

from ledger.canonical import GENESIS_HASH
from ledger.recorder import Actor, PolicyRefusal, Recorder, Target
from ledger.store import LedgerStore
from ledger.validation import RecordValidationError

ROOT = Path(__file__).resolve().parent.parent
SOCKET = ROOT / ".pgdata" / "sockets"
ADMIN_DSN = f"host={SOCKET} port=5433 user=postgres dbname=postgres"

pytestmark = pytest.mark.skipif(
    not (ROOT / ".pgdata" / "postmaster.pid").exists(),
    reason="local PostgreSQL cluster is not running (scripts/pg.sh start)",
)


@pytest.fixture(scope="session")
def test_db():
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
    return LedgerStore(test_db)


@pytest.fixture
def recorder(store):
    return Recorder(
        incident_id=f"INC-{uuid.uuid4().hex[:6]}",
        actor=Actor("test-agent", "0.0.1", "test", "inv-1"),
        target=Target("k8s_deployment", "staging/api", "staging"),
        store=store,
    )


def evidence(recorder):
    return [recorder.capture(evidence_type="log_query", source="q", content="line")]


def test_first_record_starts_the_chain_at_genesis(recorder, store):
    res = recorder.emit(action_type="investigate", summary="s", outcome_status="success",
                        outcome_details="d", evidence=evidence(recorder))
    if res.seq == 1:
        assert res.prev_record_hash == GENESIS_HASH


def test_each_record_chains_to_its_predecessor(recorder, store):
    first = recorder.emit(action_type="investigate", summary="a", outcome_status="success",
                          outcome_details="d", evidence=evidence(recorder))
    second = recorder.emit(action_type="investigate", summary="b", outcome_status="success",
                           outcome_details="d", evidence=evidence(recorder))
    assert second.prev_record_hash == first.record_hash


def test_generated_columns_track_the_payload(recorder, store):
    res = recorder.emit(action_type="investigate", summary="s", outcome_status="success",
                        outcome_details="d", evidence=evidence(recorder))
    with store.connect() as conn:
        row = conn.execute(
            "SELECT action_type, environment, outcome_status, resource_id, "
            "actor_agent_name, record_hash, event_time "
            "FROM agent_action_records WHERE record_id = %s", (res.record_id,)
        ).fetchone()
    assert row["action_type"] == "investigate"
    assert row["environment"] == "staging"
    assert row["outcome_status"] == "success"
    assert row["record_hash"] == res.record_hash
    assert row["event_time"] is not None


def test_update_is_refused_even_for_the_owner(store):
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE agent_action_records SET record = '{}'::jsonb")


def test_delete_is_refused_even_for_the_owner(store):
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM agent_action_records")


def test_truncate_is_refused(store):
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("TRUNCATE agent_action_records")


def test_mutation_is_refused_on_an_empty_table_too(test_db):
    """The guarantee must hold from day zero, before any record exists."""
    empty = f"{test_db}_empty"
    name = f"swarmops_empty_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    dsn = f"host={SOCKET} port=5433 user=postgres dbname={name}"
    try:
        with psycopg.connect(dsn) as conn:
            for sql in sorted((ROOT / "ledger" / "sql").glob("*.sql")):
                conn.execute(sql.read_text())
            conn.commit()
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute("DELETE FROM agent_action_records")
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_duplicate_record_hash_is_rejected(store, recorder):
    """Replaying an existing record must not be possible.

    The seq is supplied explicitly because seq is no longer a sequence-backed
    column; the point of the test is the uniqueness constraint on record_hash,
    which is what stops an attacker re-inserting a record they captured.
    """
    res = recorder.emit(action_type="investigate", summary="s", outcome_status="success",
                        outcome_details="d", evidence=evidence(recorder))
    with store.connect() as conn:
        next_seq = conn.execute(
            "SELECT COALESCE(max(seq), 0) + 1 AS n FROM agent_action_records"
        ).fetchone()["n"]
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO agent_action_records (seq, record) VALUES (%s, %s)",
                         (next_seq, json.dumps(res.record)))


def test_invalid_record_never_reaches_the_table(store, recorder):
    before = store.count()
    with pytest.raises(RecordValidationError):
        # No evidence on an investigate record violates the schema.
        recorder.emit(action_type="investigate", summary="s", outcome_status="success",
                      outcome_details="d", evidence=[])
    assert store.count() == before


def test_policy_refusal_is_itself_recorded(store, recorder):
    before = store.count()
    with pytest.raises(PolicyRefusal) as exc:
        recorder.emit(
            action_type="execute", summary="irreversible", outcome_status="success",
            outcome_details="d", evidence=evidence(recorder),
            proposed_action={"description": "d", "change_type": "schema_migration",
                             "command_or_diff": "DROP TABLE x", "reversible": False,
                             "blast_radius": "single_service"},
            approver={"approver_id": "u", "approver_type": "human",
                      "approved_at": "2026-09-18T14:30:00Z", "approval_channel": "cli"},
            in_response_to=str(uuid.uuid4()),
        )
    # A blocked action is exactly what an auditor wants to see; it must not be
    # dropped just because it did not happen.
    assert store.count() == before + 1
    refusal = store.get(exc.value.record_id)
    assert refusal["outcome"]["status"] == "failure"
    assert "REFUSED BY POLICY" in refusal["summary"]


def test_whole_chain_verifies(store, recorder):
    from scripts.verify_chain import verify

    for i in range(4):
        recorder.emit(action_type="investigate", summary=f"s{i}", outcome_status="success",
                      outcome_details="d", evidence=evidence(recorder))
    report = verify(store)
    assert report.ok, [f.as_dict() for f in report.findings]
    assert report.verified == report.total


def test_verifier_detects_an_edited_record(store, recorder):
    from scripts.verify_chain import verify

    res = recorder.emit(action_type="investigate", summary="original", outcome_status="success",
                        outcome_details="d", evidence=evidence(recorder))
    tampered = dict(res.record)
    tampered["summary"] = "quietly rewritten"
    with store.connect() as conn:
        conn.execute("ALTER TABLE agent_action_records DISABLE TRIGGER USER")
        conn.execute("UPDATE agent_action_records SET record = %s WHERE record_id = %s",
                     (json.dumps(tampered), res.record_id))
        conn.execute("ALTER TABLE agent_action_records ENABLE TRIGGER USER")
        conn.commit()

    report = verify(store)
    assert not report.ok
    assert any(f.kind == "hash_mismatch" for f in report.findings)


def test_verifier_detects_a_deleted_record(store, recorder):
    from scripts.verify_chain import verify

    recorder.emit(action_type="investigate", summary="a", outcome_status="success",
                  outcome_details="d", evidence=evidence(recorder))
    victim = recorder.emit(action_type="investigate", summary="b", outcome_status="success",
                           outcome_details="d", evidence=evidence(recorder))
    recorder.emit(action_type="investigate", summary="c", outcome_status="success",
                  outcome_details="d", evidence=evidence(recorder))
    with store.connect() as conn:
        conn.execute("ALTER TABLE agent_action_records DISABLE TRIGGER USER")
        conn.execute("DELETE FROM agent_action_records WHERE record_id = %s", (victim.record_id,))
        conn.execute("ALTER TABLE agent_action_records ENABLE TRIGGER USER")
        conn.commit()

    report = verify(store)
    assert not report.ok
    kinds = {f.kind for f in report.findings}
    # Deletion shows up two ways, and both matter: the link no longer resolves,
    # and the sequence has a hole.
    assert "chain_break" in kinds
    assert "sequence_gap" in kinds


# ------------------------------------------------------------------ executor


@pytest.fixture
def service_calls(monkeypatch):
    """Replace the seeded service with a fake; return the list of calls it got."""
    from approval import executor as executor_module

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def _reply(self, method, url):
            calls.append((method, url))
            return type("Resp", (), {"status_code": 200, "text": '{"ok": true}'})()

        def get(self, url, **kwargs):
            return self._reply("GET", url)

        def post(self, url, **kwargs):
            return self._reply("POST", url)

    monkeypatch.setattr(executor_module.httpx, "Client", FakeClient)
    monkeypatch.setattr(executor_module.time, "sleep", lambda s: None)
    return calls


def _approved_proposal(recorder, *, reversible=True, change_type="rollback_deploy"):
    """Write investigate -> propose_fix -> request_approval -> approve; return the approve id."""
    fix = {
        "description": "Roll back to v1.4.1", "change_type": change_type,
        "command_or_diff": "kubectl rollout undo deployment/api -n staging",
        "reversible": reversible, "blast_radius": "single_service",
    }
    plan = {**fix, "remediation_action": "rollback_deploy",
            "remediation_params": {"target_version": "v1.4.1"}}
    found = recorder.emit(action_type="investigate", summary="found it", outcome_status="success",
                          outcome_details="d", evidence=evidence(recorder))
    proposal = recorder.emit(
        action_type="propose_fix", summary="Proposed rollback", outcome_status="pending",
        outcome_details="d", proposed_action=fix, in_response_to=found.record_id,
        evidence=[recorder.capture(evidence_type="diff", source="plan", content=json.dumps(plan))],
    )
    request = recorder.emit(action_type="request_approval", summary="Approval requested",
                            outcome_status="pending", outcome_details="d",
                            in_response_to=proposal.record_id)
    approval = recorder.emit(
        action_type="approve", summary="Approved", outcome_status="success", outcome_details="d",
        in_response_to=request.record_id,
        approver={"approver_id": "dev|alice", "approver_type": "human",
                  "approved_at": "2026-01-01T00:00:00Z", "approval_channel": "cli"},
    )
    return approval.record_id


@pytest.mark.parametrize("proposal, refused_by", [
    ({"reversible": False}, "rule:deny_irreversible_change"),
    ({"change_type": "code_change"}, "default_deny"),
])
def test_executor_does_not_touch_the_service_when_policy_refuses(
        store, recorder, service_calls, proposal, refused_by):
    from approval.executor import Executor

    approve_id = _approved_proposal(recorder, **proposal)
    result = Executor(store=store).execute(approve_id)

    assert not result.success
    assert service_calls == [], "a refused change must not reach the service"
    refusal = store.get(result.execute_record_id)
    assert refusal["action_type"] == "execute"
    assert refusal["outcome"]["status"] == "failure"
    assert refusal["summary"].startswith("REFUSED BY POLICY")
    assert refusal["policy_reference"]["allowed_by"] == refused_by


def test_executor_applies_a_change_policy_allows(store, recorder, service_calls):
    from approval.executor import Executor

    approve_id = _approved_proposal(recorder)
    result = Executor(store=store).execute(approve_id)

    assert result.success
    assert ("POST", "http://127.0.0.1:8081/admin/apply_fix") in service_calls
    record = store.get(result.execute_record_id)
    assert record["outcome"]["status"] == "success"
    assert record["policy_reference"]["allowed_by"] == "rule:human_approved_reversible_rollback"
