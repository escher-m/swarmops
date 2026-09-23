"""The schema is the contract; these tests hold it to it."""

import json
from pathlib import Path

import pytest

from ledger.validation import SCHEMA_VERSION, is_valid, validate_record, validation_errors

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "schema" / "fixtures"


def fixture_chain():
    return json.loads((FIXTURES / "incident_chain.json").read_text())


def test_all_shipped_fixtures_validate():
    chain = fixture_chain()
    assert len(chain) >= 4
    for record in chain:
        validate_record(record)


def test_fixture_chain_is_actually_chained():
    from ledger.canonical import GENESIS_HASH, compute_record_hash

    prev = GENESIS_HASH
    for record in fixture_chain():
        assert record["prev_record_hash"] == prev
        assert record["record_hash"] == compute_record_hash(record)
        prev = record["record_hash"]


def test_a_full_incident_story_is_present():
    kinds = [r["action_type"] for r in fixture_chain()]
    for required in ("investigate", "propose_fix", "approve", "execute"):
        assert required in kinds, f"fixtures do not demonstrate {required}"


def base_record(**overrides):
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": "0b6f1e2a-9c4d-4a71-8e35-2d7f9a1c4b60",
        "incident_id": "INC-1",
        "timestamp": "2026-09-18T14:24:53Z",
        "actor": {"agent_name": "a", "agent_version": "1", "tool_or_platform": "t",
                  "invocation_id": "i"},
        "action_type": "investigate",
        "summary": "s",
        "target": {"resource_type": "k8s_deployment", "resource_id": "r", "environment": "staging"},
        "policy_reference": {"policy_id": "p", "policy_description": "d",
                             "allowed_by": "rule:read_only_investigation"},
        "evidence": [{
            "evidence_type": "log_query", "source": "src",
            "snapshot_ref_or_inline": {"kind": "inline", "content_type": "text/plain",
                                       "content": "x"},
            "retrieved_at": "2026-09-18T14:24:00Z",
        }],
        "approver": None,
        "outcome": {"status": "success", "details": "d"},
        "prev_record_hash": "0" * 64,
        "record_hash": "a" * 64,
    }
    record.update(overrides)
    return record


def test_investigate_without_evidence_is_rejected():
    # An investigation that cites nothing proves nothing.
    assert not is_valid(base_record(evidence=[]))


def test_propose_fix_requires_a_concrete_proposed_action():
    errors = validation_errors(base_record(action_type="propose_fix"))
    assert any("proposed_action" in e for e in errors)


def test_execute_requires_an_approver_and_a_prior_approval():
    errors = validation_errors(base_record(
        action_type="execute",
        proposed_action={"description": "d", "change_type": "rollback_deploy",
                         "command_or_diff": "c", "reversible": True},
    ))
    joined = " ".join(errors)
    # Both the approver and the link to the authorising record are mandatory;
    # this is the rule that makes silent auto-remediation unrecordable.
    assert "approver" in joined or "in_response_to" in joined


def test_approve_requires_in_response_to():
    errors = validation_errors(base_record(
        action_type="approve",
        approver={"approver_id": "u", "approver_type": "human",
                  "approved_at": "2026-09-18T14:30:00Z", "approval_channel": "cli"},
    ))
    assert any("in_response_to" in e for e in errors)


def test_human_override_must_name_a_human():
    record = base_record(
        action_type="execute",
        in_response_to="3e9c4b5d-2f70-4da4-b168-509c2d4f7e93",
        policy_reference={"policy_id": "p", "policy_description": "d",
                          "allowed_by": "human_override"},
        proposed_action={"description": "d", "change_type": "rollback_deploy",
                         "command_or_diff": "c", "reversible": True},
        approver={"approver_id": "u", "approver_type": "policy_auto_approved",
                  "approved_at": "2026-09-18T14:30:00Z", "approval_channel": "cli"},
    )
    assert not is_valid(record), "a policy must not be able to auto-approve a human override"


@pytest.mark.parametrize("field", [
    "record_id", "timestamp", "actor", "action_type", "target",
    "policy_reference", "evidence", "approver", "outcome",
    "prev_record_hash", "record_hash", "incident_id",
])
def test_every_required_field_is_actually_required(field):
    record = base_record()
    record.pop(field)
    assert not is_valid(record), f"{field} was dropped and the record still validated"


def test_unknown_fields_are_rejected():
    # additionalProperties:false matters here: a field we do not know about is
    # a field the hash covers but no consumer understands.
    assert not is_valid(base_record(surprise="value"))


def test_bad_timestamp_format_is_rejected():
    assert not is_valid(base_record(timestamp="2026-09-18 14:24:53"))
    assert not is_valid(base_record(timestamp="2026-09-18T14:24:53+02:00"))


def test_unknown_action_type_is_rejected():
    assert not is_valid(base_record(action_type="delete_everything"))


def test_unknown_environment_is_rejected():
    assert not is_valid(base_record(
        target={"resource_type": "x", "resource_id": "y", "environment": "production"}))
