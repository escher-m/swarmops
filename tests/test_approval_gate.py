"""The approval gate against a real PostgreSQL ledger: how overrides are recorded."""

import json

import pytest

from approval.gate import ApprovalGate
from ledger.policy import evaluate


def _pending_request(recorder, store, **fix_overrides):
    """Write investigate -> propose_fix -> request_approval; return the request id."""
    fix = {
        "description": "Patch the checkout handler", "change_type": "rollback_deploy",
        "command_or_diff": "git revert 4f2c9a1", "reversible": True,
        "blast_radius": "single_service", **fix_overrides,
    }
    found = recorder.emit(
        action_type="investigate", summary="found it", outcome_status="success",
        outcome_details="d",
        evidence=[recorder.capture(evidence_type="log_query", source="q", content="line")],
    )
    proposal = recorder.emit(
        action_type="propose_fix", summary="Proposed fix", outcome_status="pending",
        outcome_details="d", proposed_action=fix, in_response_to=found.record_id,
        evidence=[recorder.capture(evidence_type="diff", source="plan", content=json.dumps(fix))],
    )
    return ApprovalGate(store).request_approval(
        incident_id=recorder.incident_id, propose_record_id=proposal.record_id)


def _execute_decision(approve, proposal):
    """Evaluate execution exactly the way approval/executor.py does."""
    pa = proposal["proposed_action"]
    return evaluate(
        action_type="execute", environment=proposal["target"]["environment"],
        change_type=pa["change_type"], reversible=pa["reversible"],
        blast_radius=pa["blast_radius"],
        human_override=approve["policy_reference"]["allowed_by"] == "human_override",
    )


def test_an_override_approval_lets_the_change_execute(recorder, store):
    request_id = _pending_request(recorder, store, change_type="code_change")
    gate = ApprovalGate(store)
    assert gate.get_pending(request_id).needs_override

    approve_id = gate.decide(request_record_id=request_id, approve=True, note="I own this",
                             approver_id="dev|alice", override=True)

    approve = store.get(approve_id)
    assert approve["policy_reference"]["allowed_by"] == "human_override"
    proposal = store.get(store.get(request_id)["in_response_to"])
    decision = _execute_decision(approve, proposal)
    assert decision.allowed
    assert decision.rule_id == "human_override"


@pytest.mark.parametrize("fix, why", [
    ({}, "policy already permits it"),
    ({"reversible": False}, "a deny rule forbids it"),
])
def test_an_override_is_refused_when_it_cannot_change_the_outcome(recorder, store, fix, why):
    request_id = _pending_request(recorder, store, **fix)
    gate = ApprovalGate(store)
    assert not gate.get_pending(request_id).needs_override, why

    with pytest.raises(ValueError, match="does not take an override"):
        gate.decide(request_record_id=request_id, approve=True, note="I own this",
                    approver_id="dev|alice", override=True)
    assert gate.get_pending(request_id), "a refused decision must leave the request open"


def test_an_ordinary_approval_cites_the_rule_that_permits_execution(recorder, store):
    request_id = _pending_request(recorder, store)
    approve_id = ApprovalGate(store).decide(request_record_id=request_id, approve=True,
                                            note="routine", approver_id="dev|alice")

    approve = store.get(approve_id)
    assert approve["policy_reference"]["allowed_by"] == "rule:human_approval_decision"
    proposal = store.get(store.get(request_id)["in_response_to"])
    assert _execute_decision(approve, proposal).rule_id == "rule:human_approved_reversible_rollback"
