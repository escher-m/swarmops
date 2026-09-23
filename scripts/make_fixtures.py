#!/usr/bin/env python3
"""Generate the example ledger records described in Component 1.

These fixtures are documentation as much as test data. Someone evaluating the
product should be able to open schema/fixtures/incident_chain.json and follow a
complete story -- what broke, what the agent looked at, what it proposed, who
approved it, what happened -- without reading any of our code.

The hashes here are real: the chain is built with the same canonicalization and
sealing code the production path uses, so the shipped fixtures independently
verify under scripts/verify_chain.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ledger.canonical import GENESIS_HASH, seal  # noqa: E402
from ledger.evidence_store import default_store  # noqa: E402
from ledger.validation import SCHEMA_VERSION, validate_record  # noqa: E402

INCIDENT = "INC-2026-0918-0042"
INVOCATION = "holmes-run-7f3a91c4"

ACTOR = {
    "agent_name": "holmesgpt-investigator",
    "agent_version": "0.12.1",
    "tool_or_platform": "holmesgpt",
    "invocation_id": INVOCATION,
    "model": "claude-sonnet-5",
}

TARGET = {
    "resource_type": "k8s_deployment",
    "resource_id": "staging/checkout-api",
    "environment": "staging",
}

READ_ONLY_POLICY = {
    "policy_id": "sre-agent-policy-v3",
    "policy_description": (
        "Autonomous agents may run read-only diagnostic queries against logs, metrics, "
        "git history and HTTP health endpoints in any environment without prior approval, "
        "provided every query and its result are recorded as evidence."
    ),
    "allowed_by": "rule:read_only_investigation",
}

PROPOSE_POLICY = {
    "policy_id": "sre-agent-policy-v3",
    "policy_description": (
        "Autonomous agents may propose remediations but may never apply a write or "
        "production-affecting change without a recorded human approval. Proposals must "
        "state the exact command or diff to be applied."
    ),
    "allowed_by": "rule:propose_only_no_autoremediation",
}

WRITE_POLICY = {
    "policy_id": "sre-agent-policy-v3",
    "policy_description": (
        "A reversible rollback of a non-production deployment may be executed by the agent "
        "once a human with the sre-oncall role has approved the specific proposed change."
    ),
    "allowed_by": "rule:human_approved_reversible_rollback",
}

# A log excerpt large enough to exercise the blob path rather than inline
# storage. Real investigations pull far more than this.
LOG_LINES = "\n".join(
    f"2026-09-18T14:1{i // 60}:{i % 60:02d}.{(i * 37) % 1000:03d}Z checkout-api-7d9f4b6c8-{'abcde'[i % 5]}xk2 "
    f"ERROR [checkout.handler] Unhandled exception serving POST /checkout "
    f"request_id=req-{i:05d} user_id=usr-{(i * 991) % 8000:04d}\n"
    f"    Traceback (most recent call last):\n"
    f'      File "/app/checkout/handler.py", line 142, in create_order\n'
    f"        total = cart.subtotal + cart.shipping_cost\n"
    f"    TypeError: unsupported operand type(s) for +: 'Decimal' and 'NoneType'"
    for i in range(40)
)

GIT_LOG = """commit 4f2c9a1e8b3d5670a1c2e9f8b7d6c5a4e3f21098 (HEAD -> main, tag: v1.4.2)
Author: a platform engineer <redacted@example.com>
Date:   Fri Sep 18 13:41:02 2026 +0000

    feat(checkout): make shipping_cost optional for digital goods

    Digital goods have no shipping, so allow the column to be null rather than
    defaulting it to zero.

diff --git a/checkout/models.py b/checkout/models.py
@@ -31,7 +31,7 @@ class Cart(Base):
     subtotal = Column(Numeric(10, 2), nullable=False)
-    shipping_cost = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))
+    shipping_cost = Column(Numeric(10, 2), nullable=True)

commit 9a8b7c6d5e4f30291817263544536271890abcde (tag: v1.4.1)
Author: a platform engineer <redacted@example.com>
Date:   Thu Sep 17 09:12:44 2026 +0000

    chore: bump base image to python:3.12-slim
"""

ROLLBACK_DIFF = """--- deployment/checkout-api.yaml (current, v1.4.2)
+++ deployment/checkout-api.yaml (proposed, v1.4.1)
@@ -18,7 +18,7 @@ spec:
     spec:
       containers:
       - name: checkout-api
-        image: registry.internal/checkout-api:v1.4.2
+        image: registry.internal/checkout-api:v1.4.1
"""


def build_chain(store: EvidenceStore) -> list[dict]:
    # Stable ids so regenerating the fixtures produces no spurious diff. The
    # version/variant nibbles are set correctly so these satisfy the schema's
    # uuid format check rather than merely looking like UUIDs.
    ids = {
        "investigate": "0b6f1e2a-9c4d-4a71-8e35-2d7f9a1c4b60",
        "propose": "1c7a2f3b-0d5e-4b82-9f46-3e8a0b2d5c71",
        "request": "2d8b3a4c-1e6f-4c93-a057-4f9b1c3e6d82",
        "approve": "3e9c4b5d-2f70-4da4-b168-509c2d4f7e93",
        "execute": "4fad5c6e-3081-4eb5-9279-61ad3e508fa4",
    }

    approver = {
        "approver_id": "okta|00u4k2x9zQpLmNb7h",
        "approver_type": "human",
        "approved_at": "2026-09-18T14:31:07Z",
        "approval_channel": "cli",
        "approver_display_name": "on-call SRE (sre-oncall rota)",
        "decision_note": (
            "Traceback and the v1.4.2 diff agree on the cause. Rollback is reversible and "
            "staging only. Approved. Follow-up ticket filed for the null-handling fix."
        ),
    }

    records = [
        {
            "schema_version": SCHEMA_VERSION,
            "record_id": ids["investigate"],
            "incident_id": INCIDENT,
            "timestamp": "2026-09-18T14:24:53Z",
            "actor": ACTOR,
            "action_type": "investigate",
            "summary": (
                "Investigated elevated 5xx on staging/checkout-api and traced them to a "
                "TypeError introduced by release v1.4.2."
            ),
            "target": TARGET,
            "policy_reference": READ_ONLY_POLICY,
            "evidence": [
                store.store_evidence(
                    evidence_type="http_probe",
                    source="POST https://checkout-api.staging.internal/checkout",
                    retrieved_at="2026-09-18T14:23:11Z",
                    content_type="application/json",
                    content=json.dumps(
                        {
                            "status_code": 500,
                            "latency_ms": 41,
                            "body": {"error": "internal server error", "request_id": "req-00031"},
                            "probe_repeated": 5,
                            "failure_rate": "5/5",
                        },
                        indent=2,
                    ),
                ),
                store.store_evidence(
                    evidence_type="log_query",
                    source=(
                        'kubectl logs -n staging -l app=checkout-api --since=15m '
                        '| grep -E "ERROR|Traceback"'
                    ),
                    retrieved_at="2026-09-18T14:23:48Z",
                    content=LOG_LINES,
                ),
                store.store_evidence(
                    evidence_type="git_history",
                    source="git log -2 --patch -- checkout/models.py (repo: internal/checkout-api)",
                    retrieved_at="2026-09-18T14:24:20Z",
                    content=GIT_LOG,
                ),
                store.store_evidence(
                    evidence_type="agent_reasoning",
                    source="holmesgpt investigation conclusion",
                    retrieved_at="2026-09-18T14:24:51Z",
                    content=(
                        "All 40 sampled errors share one traceback: Cart.shipping_cost is None "
                        "when summed with subtotal. Release v1.4.2 (deployed 14:09Z, 14 minutes "
                        "before the first error at 14:10Z) made shipping_cost nullable without "
                        "updating the summation in handler.py line 142. Existing carts written "
                        "before the change still have zeros, which is why the failure rate is "
                        "partial rather than total. Cause is the v1.4.2 release."
                    ),
                ),
            ],
            "approver": None,
            "outcome": {
                "status": "success",
                "details": (
                    "Root cause identified: null shipping_cost introduced by v1.4.2 breaks order "
                    "total calculation in checkout/handler.py:142. 4 evidence artifacts captured."
                ),
                "duration_ms": 98420,
            },
        },
        {
            "schema_version": SCHEMA_VERSION,
            "record_id": ids["propose"],
            "incident_id": INCIDENT,
            "in_response_to": ids["investigate"],
            "timestamp": "2026-09-18T14:25:02Z",
            "actor": ACTOR,
            "action_type": "propose_fix",
            "summary": "Proposed rolling staging/checkout-api back from v1.4.2 to v1.4.1.",
            "target": TARGET,
            "policy_reference": PROPOSE_POLICY,
            "evidence": [
                store.store_evidence(
                    evidence_type="diff",
                    source="proposed manifest change for deployment staging/checkout-api",
                    retrieved_at="2026-09-18T14:25:00Z",
                    content_type="text/x-diff",
                    content=ROLLBACK_DIFF,
                ),
            ],
            "proposed_action": {
                "description": (
                    "Roll the checkout-api deployment back to image tag v1.4.1, the last release "
                    "before the nullable shipping_cost change. This restores service while the "
                    "null-handling bug in handler.py is fixed properly. It does not alter data: "
                    "carts written under v1.4.2 keep their null shipping_cost and will be handled "
                    "by v1.4.1's non-null default on next write."
                ),
                "change_type": "rollback_deploy",
                "command_or_diff": (
                    "kubectl set image deployment/checkout-api "
                    "checkout-api=registry.internal/checkout-api:v1.4.1 -n staging"
                ),
                "reversible": True,
                "blast_radius": "single_service",
            },
            "approver": None,
            "outcome": {
                "status": "pending",
                "details": "Proposal recorded. No change applied; awaiting human approval.",
            },
        },
        {
            "schema_version": SCHEMA_VERSION,
            "record_id": ids["request"],
            "incident_id": INCIDENT,
            "in_response_to": ids["propose"],
            "timestamp": "2026-09-18T14:25:04Z",
            "actor": {**ACTOR, "agent_name": "swarmops-approval-gate", "agent_version": "0.1.0"},
            "action_type": "request_approval",
            "summary": "Routed the proposed v1.4.1 rollback to the sre-oncall rota for approval.",
            "target": TARGET,
            "policy_reference": PROPOSE_POLICY,
            "evidence": [],
            "approver": None,
            "outcome": {
                "status": "pending",
                "details": "Approval request delivered to channel 'cli'. Awaiting decision.",
            },
        },
        {
            "schema_version": SCHEMA_VERSION,
            "record_id": ids["approve"],
            "incident_id": INCIDENT,
            "in_response_to": ids["request"],
            "timestamp": "2026-09-18T14:31:07Z",
            "actor": {**ACTOR, "agent_name": "swarmops-approval-gate", "agent_version": "0.1.0"},
            "action_type": "approve",
            "summary": "On-call SRE approved the v1.4.1 rollback of staging/checkout-api.",
            "target": TARGET,
            "policy_reference": WRITE_POLICY,
            "evidence": [],
            "approver": approver,
            "outcome": {
                "status": "success",
                "details": "Approved after 6m03s. Execution authorized for this proposal only.",
                "duration_ms": 363000,
            },
        },
        {
            "schema_version": SCHEMA_VERSION,
            "record_id": ids["execute"],
            "incident_id": INCIDENT,
            "in_response_to": ids["approve"],
            "timestamp": "2026-09-18T14:31:12Z",
            "actor": {**ACTOR, "agent_name": "swarmops-executor", "agent_version": "0.1.0"},
            "action_type": "execute",
            "summary": "Applied the approved rollback; staging/checkout-api is serving 200s again.",
            "target": TARGET,
            "policy_reference": WRITE_POLICY,
            "evidence": [
                store.store_evidence(
                    evidence_type="command_output",
                    source=(
                        "kubectl set image deployment/checkout-api "
                        "checkout-api=registry.internal/checkout-api:v1.4.1 -n staging"
                    ),
                    retrieved_at="2026-09-18T14:31:14Z",
                    content=(
                        "deployment.apps/checkout-api image updated\n"
                        "Waiting for deployment rollout to finish: 1 of 3 updated replicas...\n"
                        "Waiting for deployment rollout to finish: 2 of 3 updated replicas...\n"
                        'deployment "checkout-api" successfully rolled out'
                    ),
                ),
                store.store_evidence(
                    evidence_type="http_probe",
                    source="POST https://checkout-api.staging.internal/checkout (post-change verification)",
                    retrieved_at="2026-09-18T14:32:40Z",
                    content_type="application/json",
                    content=json.dumps(
                        {
                            "status_code": 200,
                            "latency_ms": 63,
                            "probe_repeated": 10,
                            "failure_rate": "0/10",
                            "note": "verified 88s after rollout completed",
                        },
                        indent=2,
                    ),
                ),
            ],
            "proposed_action": {
                "description": "Executed the approved rollback to v1.4.1, unchanged from the approved proposal.",
                "change_type": "rollback_deploy",
                "command_or_diff": (
                    "kubectl set image deployment/checkout-api "
                    "checkout-api=registry.internal/checkout-api:v1.4.1 -n staging"
                ),
                "reversible": True,
                "blast_radius": "single_service",
            },
            "approver": approver,
            "outcome": {
                "status": "success",
                "details": (
                    "Rollout completed in 84s. Post-change probe: 10/10 requests returned 200. "
                    "Error rate returned to baseline (0 errors in the 5 minutes following)."
                ),
                "duration_ms": 84300,
            },
        },
    ]

    # Seal the chain: each record commits to its predecessor's hash.
    sealed: list[dict] = []
    prev = GENESIS_HASH
    for record in records:
        record["prev_record_hash"] = prev
        done = seal(record)
        validate_record(done)
        sealed.append(done)
        prev = done["record_hash"]
    return sealed


def main() -> int:
    store = default_store()
    chain = build_chain(store)

    out_dir = ROOT / "schema" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "incident_chain.json").write_text(json.dumps(chain, indent=2) + "\n")
    for record in chain:
        name = f"{record['action_type']}_{record['record_id'][:8]}.json"
        (out_dir / name).write_text(json.dumps(record, indent=2) + "\n")

    print(f"wrote {len(chain)} validated records to {out_dir}")
    for record in chain:
        print(f"  {record['action_type']:<16} {record['record_id']}  hash={record['record_hash'][:12]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
