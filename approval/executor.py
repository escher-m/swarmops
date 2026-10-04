"""Applies an approved remediation, and records what actually happened.

Two properties are worth calling out, because they are what make the execute
record trustworthy rather than merely present.

First, the executor re-derives its authorisation from the ledger. It is given
an approve record id and goes and reads that record, the request it answered,
and the proposal that was requested -- it does not accept a caller's assertion
that something was approved. An attacker who can call this function still has
to have got an approval record into an append-only ledger.

Second, what runs is taken from the immutable evidence blob attached to the
proposal, not from a parameter. The approver saw that blob; its content is
addressed by its own hash; so the action executed is provably the action
approved, not a re-interpretation of it made later.

Every execution is bracketed by a probe before and after, so the outcome field
records measured recovery rather than an assumption that the command worked.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from approval.gate import GATE_VERSION
from ledger.evidence_store import default_store
from ledger.policy import evaluate
from ledger.recorder import Actor, PolicyRefusal, Recorder, Target
from ledger.store import LedgerStore

EXECUTOR_VERSION = "0.1.0"

# Which endpoint each remediation probes to demonstrate recovery.
VERIFICATION_PROBE = {
    "rollback_deploy": ("POST", "/checkout", {"digital_only": True}),
    "increase_pool_size": ("GET", "/orders", None),
    "restore_revenue_index": ("GET", "/reports/revenue", None),
    "rotate_credential": ("GET", "/admin/state", None),
}


class ExecutionError(RuntimeError):
    pass


@dataclass
class ExecutionResult:
    execute_record_id: str
    success: bool
    detail: str
    duration_ms: int


class Executor:
    def __init__(self, *, seedapp_base: str = "http://127.0.0.1:8081", store: LedgerStore | None = None):
        self.base = seedapp_base.rstrip("/")
        self.store = store or LedgerStore()
        self.evidence = default_store()

    # ------------------------------------------------------------- authority

    def _authorisation_chain(self, approve_record_id: str) -> tuple[dict, dict, dict]:
        """Walk approve -> request_approval -> propose_fix, from the ledger."""
        approve = self.store.get(approve_record_id)
        if approve is None:
            raise ExecutionError(f"no such record: {approve_record_id}")
        if approve["action_type"] != "approve":
            raise ExecutionError(
                f"record {approve_record_id} is a {approve['action_type']} record; "
                f"only an approve record authorises execution"
            )
        if not approve.get("approver"):
            raise ExecutionError(f"approve record {approve_record_id} names no approver")

        request = self.store.get(approve.get("in_response_to") or "")
        if request is None or request["action_type"] != "request_approval":
            raise ExecutionError(
                f"approve record {approve_record_id} does not answer a request_approval record"
            )
        proposal = self.store.get(request.get("in_response_to") or "")
        if proposal is None or proposal["action_type"] != "propose_fix":
            raise ExecutionError(
                f"request_approval {request['record_id']} does not reference a propose_fix record"
            )
        return approve, request, proposal

    def _plan_from_evidence(self, proposal: dict) -> dict[str, Any]:
        """Recover the machine-executable plan from the proposal's evidence.

        Reading it back out of the content-addressed store, rather than
        accepting it as an argument, is what ties execution to the exact bytes
        the approver was shown.
        """
        for item in proposal.get("evidence", []):
            ref = item.get("snapshot_ref_or_inline") or {}
            if item.get("evidence_type") != "diff":
                continue
            if ref.get("kind") == "inline":
                raw = ref.get("content", "")
            else:
                raw = self.evidence.get(ref.get("sha256", "")).decode("utf-8")
            try:
                plan = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "remediation_action" in plan:
                return plan
        raise ExecutionError(
            f"proposal {proposal['record_id']} carries no machine-executable remediation plan; "
            f"refusing to guess what was approved"
        )

    # ------------------------------------------------------------- execution

    def _probe(self, action: str) -> tuple[str, str]:
        method, path, body = VERIFICATION_PROBE.get(action, ("GET", "/health", None))
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = (
                    client.post(self.base + path, json=body)
                    if method == "POST" else client.get(self.base + path)
                )
            elapsed = (time.perf_counter() - started) * 1000
            return (
                f"{method} {path}",
                json.dumps({
                    "status_code": resp.status_code,
                    "latency_ms": round(elapsed, 1),
                    "body": resp.text[:1500],
                }, indent=2),
            )
        except Exception as exc:
            return f"{method} {path}", json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2)

    def execute(self, approve_record_id: str) -> ExecutionResult:
        approve, request, proposal = self._authorisation_chain(approve_record_id)
        plan = self._plan_from_evidence(proposal)
        pa = proposal.get("proposed_action", {})
        action = plan["remediation_action"]
        params = plan.get("remediation_params") or {}

        # Re-evaluate policy at execution time. The world may have changed since
        # the proposal was written, and the ledger should reflect the decision
        # that was actually in force when the command ran. The Recorder evaluates
        # again when it writes, so it must get the same override flag.
        override = approve["policy_reference"]["allowed_by"] == "human_override"
        decision = evaluate(
            action_type="execute",
            environment=proposal["target"]["environment"],
            change_type=pa.get("change_type"),
            reversible=pa.get("reversible"),
            blast_radius=pa.get("blast_radius"),
            human_override=override,
        )

        recorder = Recorder(
            incident_id=proposal["incident_id"],
            actor=Actor(
                agent_name="swarmops-executor",
                agent_version=EXECUTOR_VERSION,
                tool_or_platform="swarmops",
                invocation_id=f"exec-{approve_record_id[:8]}",
            ),
            target=Target(**proposal["target"]),
            store=self.store,
        )

        # Refuse before touching the service. The refusal record says "No
        # change was made", so the service must not be called on this path.
        if not decision.allowed:
            try:
                recorder.emit(
                    action_type="execute",
                    summary=(
                        f"Did not apply the approved remediation '{action}' on "
                        f"{proposal['target']['resource_id']}."
                    )[:500],
                    # The Recorder writes its own refusal status and details.
                    outcome_status="failure",
                    outcome_details="",
                    proposed_action=pa,
                    approver=approve["approver"],
                    in_response_to=approve_record_id,
                    human_override=override,
                )
            except PolicyRefusal as refusal:
                return ExecutionResult(
                    execute_record_id=refusal.record_id, success=False,
                    detail=str(refusal), duration_ms=0,
                )
            raise ExecutionError(
                f"policy refused executing {approve_record_id} but the recorder allowed it"
            )

        before_src, before = self._probe(action)
        started = time.perf_counter()
        applied_detail = ""
        success = False
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.base}/admin/apply_fix",
                    json={"action": action, **params},
                )
            success = resp.status_code == 200
            applied_detail = resp.text[:1500]
        except Exception as exc:
            applied_detail = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)

        # Give the change a moment to take effect before measuring recovery.
        time.sleep(0.5)
        after_src, after = self._probe(action)
        recovered = '"status_code": 200' in after or '"status_code": 201' in after

        evidence = [
            recorder.capture(
                evidence_type="http_probe",
                source=f"{before_src} (before the change)",
                content=before, content_type="application/json",
            ),
            recorder.capture(
                evidence_type="command_output",
                source=f"POST /admin/apply_fix action={action} params={params}",
                content=applied_detail,
            ),
            recorder.capture(
                evidence_type="http_probe",
                source=f"{after_src} (after the change, verification)",
                content=after, content_type="application/json",
            ),
        ]

        overall = success and recovered
        try:
            appended = recorder.emit(
                action_type="execute",
                summary=(
                    f"{'Applied' if overall else 'Attempted'} the approved remediation "
                    f"'{action}' on {proposal['target']['resource_id']}."
                )[:500],
                outcome_status="success" if overall else "failure",
                outcome_details=(
                    f"Remediation: {action} {params or ''}\n"
                    f"Apply call: {'succeeded' if success else 'FAILED'}. {applied_detail[:400]}\n"
                    f"Post-change verification: {'recovered' if recovered else 'DID NOT recover'}.\n"
                    f"Authorised by {approve['approver']['approver_id']} at "
                    f"{approve['approver']['approved_at']} via "
                    f"{approve['approver']['approval_channel']} (approve record {approve_record_id})."
                ),
                evidence=evidence,
                proposed_action=pa,
                approver=approve["approver"],
                in_response_to=approve_record_id,
                duration_ms=duration_ms,
                human_override=override,
            )
        except PolicyRefusal as refusal:
            return ExecutionResult(
                execute_record_id=refusal.record_id, success=False,
                detail=str(refusal), duration_ms=duration_ms,
            )

        return ExecutionResult(
            execute_record_id=appended.record_id,
            success=overall,
            detail=(
                f"{action} applied and verified" if overall
                else f"{action} did not fully succeed (apply={success}, recovered={recovered})"
            ),
            duration_ms=duration_ms,
        )
