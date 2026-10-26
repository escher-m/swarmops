"""The approval gate.

Nothing in this system executes a change without passing through here, and the
gate's only power is to write records: it decides nothing on its own. That
separation is the point. `request_approval` opens a decision, `approve` or
`reject` closes it, and only a closed-as-approved decision lets the executor
run -- and the executor checks the ledger, not a variable in memory, so the
authorisation it acts on is the same one an auditor will later read.

A pending approval is defined as a `request_approval` record that no later
`approve` or `reject` record points at. Deriving it from the ledger rather than
tracking it in a side table means the queue cannot drift from the audit trail,
and there is no second source of truth to reconcile.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from typing import Any

from ledger.recorder import Actor, Recorder, Target
from ledger.store import LedgerStore, utcnow

GATE_VERSION = "0.1.0"


@dataclass
class PendingApproval:
    """One open approval decision, assembled from the incident's records."""

    request_record_id: str
    incident_id: str
    requested_at: str
    summary: str
    proposed_action: dict[str, Any]
    target: dict[str, str]
    policy_reference: dict[str, str]
    propose_record_id: str | None
    root_cause: str
    evidence_count: int
    seq: int

    @property
    def needs_override(self) -> bool:
        """True when policy refuses this change unless a human overrides it."""
        from ledger.policy import evaluate

        pa = self.proposed_action or {}
        facts = dict(
            action_type="execute",
            environment=self.target.get("environment"),
            change_type=pa.get("change_type"),
            reversible=pa.get("reversible"),
            blast_radius=pa.get("blast_radius"),
        )
        # Only when an override would actually permit it. A change a deny rule
        # forbids cannot be overridden, so asking for one would be misleading.
        return not evaluate(**facts).allowed and evaluate(**facts, human_override=True).allowed


def current_approver_id() -> str:
    """Identify the human approving from this machine.

    An OS username is a weak identity and is labelled as such rather than
    dressed up: a real deployment resolves this against an identity provider.
    Recording `local:alice` honestly is better than recording an unqualified
    `alice` that looks like an SSO subject and is not.
    """
    return os.environ.get("SWARMOPS_APPROVER_ID") or f"local:{getpass.getuser()}"


class ApprovalGate:
    def __init__(self, store: LedgerStore | None = None):
        self.store = store or LedgerStore()

    def _recorder(self, incident_id: str, target: dict[str, str], invocation_id: str) -> Recorder:
        return Recorder(
            incident_id=incident_id,
            actor=Actor(
                agent_name="swarmops-approval-gate",
                agent_version=GATE_VERSION,
                tool_or_platform="swarmops",
                invocation_id=invocation_id,
            ),
            target=Target(**target),
            store=self.store,
        )

    # ------------------------------------------------------------- requesting

    def request_approval(self, *, incident_id: str, propose_record_id: str) -> str:
        """Open an approval decision for an existing propose_fix record."""
        proposal = self.store.get(propose_record_id)
        if proposal is None:
            raise LookupError(f"no such record: {propose_record_id}")
        if proposal["action_type"] != "propose_fix":
            raise ValueError(
                f"record {propose_record_id} is a {proposal['action_type']} record, "
                f"not a propose_fix"
            )

        recorder = self._recorder(
            incident_id, proposal["target"], proposal["actor"]["invocation_id"]
        )
        pa = proposal.get("proposed_action", {})
        appended = recorder.emit(
            action_type="request_approval",
            summary=f"Approval requested: {pa.get('description', proposal['summary'])}"[:500],
            outcome_status="pending",
            outcome_details=(
                f"Awaiting a human decision on proposal {propose_record_id}. "
                f"Change type: {pa.get('change_type')}. "
                f"Reversible: {pa.get('reversible')}. "
                f"Blast radius: {pa.get('blast_radius')}. "
                f"No change has been applied."
            ),
            evidence=[],
            in_response_to=propose_record_id,
        )
        return appended.record_id

    # --------------------------------------------------------------- querying

    def pending(self) -> list[PendingApproval]:
        """Every open approval decision, oldest first."""
        with self.store.connect() as conn:
            requests = conn.execute(
                "SELECT seq, record FROM agent_action_records "
                "WHERE action_type = 'request_approval' ORDER BY seq ASC"
            ).fetchall()
            decided = {
                r["rid"] for r in conn.execute(
                    "SELECT record ->> 'in_response_to' AS rid FROM agent_action_records "
                    "WHERE action_type IN ('approve', 'reject') "
                    "AND record ->> 'in_response_to' IS NOT NULL"
                ).fetchall()
            }

        out: list[PendingApproval] = []
        for row in requests:
            rec = row["record"]
            if rec["record_id"] in decided:
                continue
            propose_id = rec.get("in_response_to")
            proposal = self.store.get(propose_id) if propose_id else None
            pa = (proposal or {}).get("proposed_action", {})

            root_cause = ""
            if proposal:
                conclusion_id = proposal.get("in_response_to")
                conclusion = self.store.get(conclusion_id) if conclusion_id else None
                if conclusion:
                    root_cause = conclusion["outcome"]["details"]

            out.append(PendingApproval(
                request_record_id=rec["record_id"],
                incident_id=rec["incident_id"],
                requested_at=rec["timestamp"],
                summary=rec["summary"],
                proposed_action=pa,
                target=rec["target"],
                policy_reference=rec["policy_reference"],
                propose_record_id=propose_id,
                root_cause=root_cause,
                evidence_count=len((proposal or {}).get("evidence", [])),
                seq=row["seq"],
            ))
        return out

    def get_pending(self, request_record_id: str) -> PendingApproval:
        for item in self.pending():
            if item.request_record_id.startswith(request_record_id):
                return item
        raise LookupError(
            f"no pending approval matching {request_record_id!r} "
            f"(it may already have been decided)"
        )

    # --------------------------------------------------------------- deciding

    def decide(
        self,
        *,
        request_record_id: str,
        approve: bool,
        note: str,
        approver_id: str | None = None,
        channel: str = "cli",
        display_name: str | None = None,
        override: bool = False,
    ) -> str:
        """Record an approve or reject decision.

        `note` is required rather than optional. An approval with no stated
        reason is the least useful row in an audit log, and making it mandatory
        at the only place decisions are recorded is cheaper than chasing it
        later.
        """
        note = (note or "").strip()
        if not note:
            raise ValueError("a decision note is required: record why you approved or rejected")

        item = self.get_pending(request_record_id)
        if approve and override and not item.needs_override:
            raise ValueError(
                "this change does not take an override: policy either permits it "
                "already or forbids it outright"
            )
        approver = {
            "approver_id": approver_id or current_approver_id(),
            "approver_type": "human",
            "approved_at": utcnow(),
            "approval_channel": channel,
            "decision_note": note[:2000],
        }
        if display_name:
            approver["approver_display_name"] = display_name

        recorder = self._recorder(
            item.incident_id, item.target, f"approval-{item.request_record_id[:8]}"
        )
        verb = "approve" if approve else "reject"
        appended = recorder.emit(
            action_type=verb,
            summary=(
                f"{'Approved' if approve else 'Rejected'} by {approver['approver_id']}: "
                f"{item.proposed_action.get('description', item.summary)}"
            )[:500],
            outcome_status="success",
            outcome_details=(
                f"Decision: {verb}. Reason given: {note}\n"
                + (
                    "Execution is authorised for this proposal only."
                    if approve else
                    "No change will be applied. The proposal is closed."
                )
            ),
            evidence=[],
            approver=approver,
            in_response_to=item.request_record_id,
            human_override=override,
        )
        return appended.record_id

    def approval_for(self, request_record_id: str) -> dict[str, Any] | None:
        """The approve record answering a request, if one exists."""
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT record FROM agent_action_records WHERE action_type = 'approve' "
                "AND record ->> 'in_response_to' = %s ORDER BY seq DESC LIMIT 1",
                (request_record_id,),
            ).fetchone()
        return row["record"] if row else None
