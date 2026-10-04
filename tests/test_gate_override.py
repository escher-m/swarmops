"""ApprovalGate.decide holds every caller to the override rule.

No database is needed: the pending request and the recorder are stubbed, and
the rule is checked before anything would be written.
"""

from types import SimpleNamespace

import pytest

from approval.gate import ApprovalGate


@pytest.fixture
def gate(monkeypatch):
    gate = ApprovalGate(store=SimpleNamespace())
    gate.emitted = []

    def pending(needs_override):
        item = SimpleNamespace(
            request_record_id="req-1", incident_id="INC-1", summary="s",
            needs_override=needs_override, proposed_action={"description": "d"},
            target={"resource_type": "k8s_deployment", "resource_id": "staging/api",
                    "environment": "staging"},
        )
        monkeypatch.setattr(gate, "get_pending", lambda rid: item)

    def recorder(*args):
        return SimpleNamespace(emit=lambda **kw: gate.emitted.append(kw)
                               or SimpleNamespace(record_id="rec-1"))

    gate.set_pending = pending
    monkeypatch.setattr(gate, "_recorder", recorder)
    return gate


def _decide(gate, **kwargs):
    return gate.decide(request_record_id="req-1", note="why", approver_id="dev|alice", **kwargs)


def test_approving_without_a_needed_override_is_refused(gate):
    gate.set_pending(needs_override=True)
    with pytest.raises(ValueError, match="requires an explicit human override"):
        _decide(gate, approve=True)
    assert gate.emitted == [], "nothing may be recorded"


@pytest.mark.parametrize("needs_override, kwargs", [
    (True, {"approve": True, "override": True}),
    (True, {"approve": False}),
    (False, {"approve": True}),
])
def test_decisions_the_rule_allows_are_recorded(gate, needs_override, kwargs):
    gate.set_pending(needs_override=needs_override)
    _decide(gate, **kwargs)
    assert len(gate.emitted) == 1
    assert gate.emitted[0]["action_type"] == ("approve" if kwargs["approve"] else "reject")
