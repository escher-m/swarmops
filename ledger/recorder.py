"""The one way anything in this system writes to the ledger.

Every component -- the HolmesGPT adapter, the approval gate, the executor --
emits records through a Recorder rather than constructing dicts and calling the
store directly. That centralisation buys three things worth having:

  * policy is always consulted, so no record can claim an authorisation that was
    never evaluated;
  * evidence always goes through the content-addressed store, so nothing is
    cited that was not captured;
  * the actor and incident identity are carried once, so records from a single
    investigation are guaranteed to correlate.

The Recorder deliberately has no method for anything other than appending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence_store import EvidenceStore
from .policy import Decision, evaluate
from .store import AppendResult, LedgerStore, utcnow
from .validation import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent


class PolicyRefusal(PermissionError):
    """Raised when policy refuses an action. The refusal is recorded first."""

    def __init__(self, decision: Decision, record_id: str):
        self.decision = decision
        self.record_id = record_id
        super().__init__(
            f"policy refused this action ({decision.rule_id}): {decision.reason}. "
            f"The refusal is recorded as {record_id}."
        )


@dataclass
class Actor:
    agent_name: str
    agent_version: str
    tool_or_platform: str
    invocation_id: str
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "agent_name": self.agent_name,
            "agent_version": self.agent_version,
            "tool_or_platform": self.tool_or_platform,
            "invocation_id": self.invocation_id,
        }
        if self.model:
            out["model"] = self.model
        return out


@dataclass
class Target:
    resource_type: str
    resource_id: str
    environment: str

    def as_dict(self) -> dict[str, str]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "environment": self.environment,
        }


@dataclass
class Recorder:
    """Emits ledger records for one incident, on behalf of one actor."""

    incident_id: str
    actor: Actor
    target: Target
    store: LedgerStore = field(default_factory=LedgerStore)
    evidence_store: EvidenceStore = field(
        default_factory=lambda: EvidenceStore(ROOT / "evidence_store")
    )

    # ------------------------------------------------------------- evidence

    def capture(
        self,
        *,
        evidence_type: str,
        source: str,
        content: str | bytes,
        content_type: str = "text/plain",
        retrieved_at: str | None = None,
        redacted: bool = False,
    ) -> dict[str, Any]:
        """Capture one evidence artifact, inline or as a blob by size."""
        return self.evidence_store.store_evidence(
            evidence_type=evidence_type,
            source=source,
            content=content,
            content_type=content_type,
            retrieved_at=retrieved_at or utcnow(),
            redacted=redacted,
        )

    # -------------------------------------------------------------- writing

    def emit(
        self,
        *,
        action_type: str,
        summary: str,
        outcome_status: str,
        outcome_details: str,
        evidence: list[dict[str, Any]] | None = None,
        proposed_action: dict[str, Any] | None = None,
        approver: dict[str, Any] | None = None,
        in_response_to: str | None = None,
        duration_ms: int | None = None,
        actor: Actor | None = None,
        target: Target | None = None,
        human_override: bool = False,
        enforce: bool = True,
    ) -> AppendResult:
        """Evaluate policy, then append the record.

        When policy refuses and `enforce` is set, the refusal itself is written
        to the ledger before raising. An attempted-but-blocked action is exactly
        the kind of event an auditor wants to see, and dropping it would make
        the ledger a record of successes only.
        """
        tgt = target or self.target
        pa = proposed_action or {}
        decision = evaluate(
            action_type=action_type,
            environment=tgt.environment,
            change_type=pa.get("change_type"),
            reversible=pa.get("reversible"),
            blast_radius=pa.get("blast_radius"),
            human_override=human_override,
        )

        if not decision.allowed and enforce:
            refusal = self._append(
                action_type=action_type,
                summary=f"REFUSED BY POLICY: {summary}",
                outcome_status="failure",
                outcome_details=(
                    f"Policy {decision.policy_id} refused this action under "
                    f"{decision.rule_id}. {decision.reason} No change was made."
                ),
                evidence=evidence,
                proposed_action=proposed_action,
                approver=approver,
                in_response_to=in_response_to,
                duration_ms=duration_ms,
                decision=decision,
                actor=actor,
                target=tgt,
            )
            raise PolicyRefusal(decision, refusal.record_id)

        return self._append(
            action_type=action_type,
            summary=summary,
            outcome_status=outcome_status,
            outcome_details=outcome_details,
            evidence=evidence,
            proposed_action=proposed_action,
            approver=approver,
            in_response_to=in_response_to,
            duration_ms=duration_ms,
            decision=decision,
            actor=actor,
            target=tgt,
        )

    def _append(
        self, *, action_type, summary, outcome_status, outcome_details, evidence,
        proposed_action, approver, in_response_to, duration_ms, decision, actor, target,
    ) -> AppendResult:
        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "incident_id": self.incident_id,
            "timestamp": utcnow(),
            "actor": (actor or self.actor).as_dict(),
            "action_type": action_type,
            "summary": summary[:500],
            "target": target.as_dict(),
            "policy_reference": decision.as_policy_reference(),
            "evidence": evidence or [],
            "approver": approver,
            "outcome": {"status": outcome_status, "details": outcome_details[:5000]},
        }
        if in_response_to:
            record["in_response_to"] = in_response_to
        if proposed_action:
            record["proposed_action"] = proposed_action
        if duration_ms is not None:
            record["outcome"]["duration_ms"] = max(0, int(duration_ms))
        return self.store.append(record)
