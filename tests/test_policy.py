"""Policy must default to deny and must fail closed on missing facts."""

import pytest

from ledger.policy import evaluate, policy_digest, policy_id, rules


def test_policy_loads_and_has_a_stable_digest():
    assert policy_id() == "sre-agent-policy-v3"
    assert policy_digest().startswith("sha256:")
    assert policy_digest() == policy_digest()


def test_every_rule_has_an_id_and_a_description():
    for rule in rules():
        assert rule.get("id")
        assert rule.get("description", "").strip()


def test_read_only_investigation_needs_no_approval_anywhere():
    for env in ("dev", "staging", "prod"):
        d = evaluate(action_type="investigate", environment=env)
        assert d.allowed and not d.requires_approval
        assert d.rule_id == "rule:read_only_investigation"


def test_proposing_is_allowed_but_is_not_acting():
    d = evaluate(action_type="propose_fix", environment="prod")
    assert d.allowed
    assert d.rule_id == "rule:propose_only_no_autoremediation"


def test_reversible_staging_rollback_is_allowed_with_approval():
    d = evaluate(action_type="execute", environment="staging",
                 change_type="rollback_deploy", reversible=True,
                 blast_radius="single_service")
    assert d.allowed and d.requires_approval


def test_irreversible_change_is_denied_even_though_another_rule_would_allow_it():
    # An explicit deny must beat a matching allow, otherwise the deny is
    # cosmetic and can be routed around.
    d = evaluate(action_type="execute", environment="staging",
                 change_type="schema_migration", reversible=False,
                 blast_radius="single_service")
    assert not d.allowed
    assert d.rule_id == "rule:deny_irreversible_change"


def test_environment_wide_blast_radius_is_denied_even_when_reversible():
    d = evaluate(action_type="execute", environment="staging",
                 change_type="config_change", reversible=True,
                 blast_radius="environment_wide")
    assert not d.allowed
    assert d.rule_id == "rule:deny_environment_wide_blast_radius"


@pytest.mark.parametrize("missing", ["reversible", "blast_radius", "change_type"])
def test_unknown_facts_fail_closed(missing):
    facts = dict(action_type="execute", environment="staging",
                 change_type="rollback_deploy", reversible=True,
                 blast_radius="single_service")
    facts[missing] = None
    d = evaluate(**facts)
    assert not d.allowed, f"a missing {missing} must not be treated as satisfied"


def test_unmatched_action_defaults_to_deny():
    d = evaluate(action_type="execute", environment="prod",
                 change_type="code_change", reversible=True,
                 blast_radius="single_service")
    assert not d.allowed
    assert d.rule_id == "default_deny"


def test_human_override_is_only_available_when_explicitly_invoked():
    facts = dict(action_type="execute", environment="prod", change_type="code_change",
                 reversible=True, blast_radius="single_service")
    assert not evaluate(**facts).allowed
    overridden = evaluate(**facts, human_override=True)
    assert overridden.allowed
    assert overridden.rule_id == "human_override"
    assert overridden.requires_approval


def test_decision_renders_a_usable_policy_reference():
    ref = evaluate(action_type="investigate", environment="dev").as_policy_reference()
    assert set(ref) >= {"policy_id", "policy_description", "allowed_by", "policy_digest"}
    assert ref["allowed_by"] != "RBAC"
    assert "\n" not in ref["policy_description"]


def test_recording_an_approval_decision_is_itself_permitted():
    # Recording a decision is a distinct action from executing the change it
    # approves. Without this rule the gate cannot write an approve record at
    # all, and the whole approval path default-denies.
    for verb in ("approve", "reject"):
        d = evaluate(action_type=verb, environment="staging")
        assert d.allowed, f"{verb} must be recordable"
        assert d.rule_id == "rule:human_approval_decision"
        assert not d.requires_approval


def test_approving_does_not_by_itself_permit_execution():
    # The separation matters: an approval can be recorded for a change that
    # policy will still refuse to execute.
    assert evaluate(action_type="approve", environment="prod").allowed
    assert not evaluate(action_type="execute", environment="prod",
                        change_type="schema_migration", reversible=False,
                        blast_radius="single_service").allowed


def test_an_approval_given_as_an_override_cites_the_override():
    # rule:human_approval_decision matches every approval, so if it were cited
    # here the executor could never tell an override approval from a plain one.
    d = evaluate(action_type="approve", environment="staging", human_override=True)
    assert d.allowed
    assert d.rule_id == "human_override"


def test_an_override_never_beats_an_explicit_deny():
    d = evaluate(action_type="execute", environment="staging", change_type="rollback_deploy",
                 reversible=False, blast_radius="single_service", human_override=True)
    assert not d.allowed
    assert d.rule_id == "rule:deny_irreversible_change"


def test_an_override_does_not_replace_a_rule_that_already_permits_execution():
    d = evaluate(action_type="execute", environment="staging", change_type="rollback_deploy",
                 reversible=True, blast_radius="single_service", human_override=True)
    assert d.rule_id == "rule:human_approved_reversible_rollback"
