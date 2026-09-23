"""Policy evaluation.

`policy_reference` on a ledger record is only worth something if a real rule was
really consulted. The spec for this product is explicit that "RBAC allowed it"
is not an acceptable answer, so this module evaluates each action against the
rules in policy/sre_agent_policy_v3.yaml and hands back the specific rule that
permitted or refused it, which the caller then records verbatim.

Two properties matter more than expressiveness here:

  Default deny. An action that matches no rule is refused. A policy engine that
  falls through to "allow" cannot be defended in an audit.

  Explicit denies win. A rule with `decision: deny` beats any allow rule that
  also matches, so `rule:deny_irreversible_change` cannot be routed around by a
  broader permission elsewhere in the file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parent.parent / "policy" / "sre_agent_policy_v3.yaml"

# Ordered least to most severe, so `blast_radius_at_most` can be a threshold.
BLAST_RADIUS_ORDER = ["single_resource", "single_service", "multi_service", "environment_wide"]


@dataclass(frozen=True)
class Decision:
    """The outcome of evaluating one action against the policy."""

    allowed: bool
    rule_id: str
    description: str
    requires_approval: bool
    policy_id: str
    policy_digest: str
    reason: str = ""

    def as_policy_reference(self) -> dict[str, str]:
        """Render the decision as the record's `policy_reference` field."""
        return {
            "policy_id": self.policy_id,
            "policy_description": " ".join(self.description.split()),
            "allowed_by": self.rule_id,
            "policy_digest": self.policy_digest,
        }


class PolicyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Any], str]:
    raw = POLICY_PATH.read_bytes()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    doc = yaml.safe_load(raw)
    if not doc or "rules" not in doc:
        raise PolicyError(f"{POLICY_PATH} does not contain a rules list")
    return doc, digest


def policy_digest() -> str:
    return _load()[1]


def policy_id() -> str:
    return _load()[0]["policy_id"]


def rules() -> list[dict[str, Any]]:
    return _load()[0]["rules"]


def _conditions_match(conditions: dict[str, Any], facts: dict[str, Any]) -> bool:
    """Every condition must hold. An absent fact fails its condition.

    Failing on absence rather than skipping is deliberate: if we cannot tell
    whether a change is reversible, we must not treat it as reversible.
    """
    for key, expected in conditions.items():
        if key == "blast_radius_at_most":
            actual = facts.get("blast_radius")
            if actual is None:
                # No stated blast radius means we cannot bound it.
                return False
            try:
                if BLAST_RADIUS_ORDER.index(actual) > BLAST_RADIUS_ORDER.index(expected):
                    return False
            except ValueError:
                return False
            continue

        actual = facts.get(key)
        if actual is None:
            return False
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def evaluate(
    *,
    action_type: str,
    environment: str | None = None,
    change_type: str | None = None,
    reversible: bool | None = None,
    blast_radius: str | None = None,
    human_override: bool = False,
) -> Decision:
    """Decide whether an action is permitted, and by which rule."""
    doc, digest = _load()
    pid = doc["policy_id"]
    facts = {
        "environment": environment,
        "change_type": change_type,
        "reversible": reversible,
        "blast_radius": blast_radius,
    }

    matched_allow: dict[str, Any] | None = None

    for rule in doc["rules"]:
        if action_type not in rule.get("applies_to", []):
            continue
        if rule["id"] == "human_override":
            # Only ever applies when a human explicitly invoked it.
            continue
        if not _conditions_match(rule.get("conditions", {}) or {}, facts):
            continue

        if rule.get("decision") == "deny":
            # An explicit deny is final and short-circuits, so no later allow
            # rule can quietly re-permit what this one forbade.
            return Decision(
                allowed=False,
                rule_id=rule["id"],
                description=rule["description"],
                requires_approval=False,
                policy_id=pid,
                policy_digest=digest,
                reason=f"explicitly denied by {rule['id']}",
            )
        if matched_allow is None:
            matched_allow = rule

    if matched_allow is not None:
        return Decision(
            allowed=True,
            rule_id=matched_allow["id"],
            description=matched_allow["description"],
            requires_approval=bool(matched_allow.get("requires_approval", False)),
            policy_id=pid,
            policy_digest=digest,
        )

    if human_override:
        override = next((r for r in doc["rules"] if r["id"] == "human_override"), None)
        if override and action_type in override.get("applies_to", []):
            return Decision(
                allowed=True,
                rule_id="human_override",
                description=override["description"],
                requires_approval=True,
                policy_id=pid,
                policy_digest=digest,
                reason="no standing rule permits this; a named human accepted responsibility",
            )

    return Decision(
        allowed=False,
        rule_id="default_deny",
        description=(
            "No rule in this policy permits the requested action. The policy's "
            "default decision is deny, so the action is refused and recorded as refused."
        ),
        requires_approval=False,
        policy_id=pid,
        policy_digest=digest,
        reason=f"no rule matches action_type={action_type} facts={facts}",
    )
