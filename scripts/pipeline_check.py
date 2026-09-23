#!/usr/bin/env python3
"""Exercise the ledger pipeline end to end without an LLM.

This runs the real fault injection, the real approval gate, the real executor
and the real seeded service. The only thing it substitutes is the reasoning:
instead of HolmesGPT deciding what is wrong, the diagnosis is scripted.

That substitution is recorded honestly. The records this produces carry
`agent_name: scripted-pipeline-check`, not `holmesgpt-investigator`, so nothing
in the ledger claims an investigation that did not happen. Use
`scripts/run_incident.py` for the real thing.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from approval.executor import Executor  # noqa: E402
from approval.gate import ApprovalGate  # noqa: E402
from ledger.recorder import Actor, Recorder, Target  # noqa: E402

BASE = "http://127.0.0.1:8081"
G, R, D, B, O = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

SCENARIO = {
    "fault": "bad_deploy",
    "root_cause": (
        "Release v1.4.2 made Cart.shipping_cost nullable without updating the order-total "
        "arithmetic in checkout/handler.py:142, so POST /checkout raises TypeError for "
        "digital-goods carts."
    ),
    "fix": {
        "description": (
            "Roll checkout-api back to v1.4.1, the last release before the nullable "
            "shipping_cost change. Reversible and confined to one service; carts written "
            "under v1.4.2 are unaffected."
        ),
        "change_type": "rollback_deploy",
        "command_or_diff": (
            "kubectl set image deployment/checkout-api "
            "checkout-api=registry.internal/checkout-api:v1.4.1 -n staging"
        ),
        "reversible": True,
        "blast_radius": "single_service",
        "remediation_action": "rollback_deploy",
        "remediation_params": {"target_version": "v1.4.1"},
    },
}


def main() -> int:
    incident_id = f"INC-PIPELINE-{int(time.time()) % 100000}"
    print(f"\n{B}Pipeline check — {incident_id}{O}")
    print(f"{D}real service, real gate, real executor; the diagnosis is scripted{O}\n")

    with httpx.Client(timeout=30) as c:
        c.post(f"{BASE}/admin/reset")
        c.post(f"{BASE}/admin/inject", json={"mode": SCENARIO["fault"]})
        before = c.post(f"{BASE}/checkout", json={"digital_only": True})
    print(f"  1. injected {SCENARIO['fault']}; POST /checkout now returns {before.status_code}")

    recorder = Recorder(
        incident_id=incident_id,
        actor=Actor("scripted-pipeline-check", "0.1.0", "swarmops", f"pipeline-{incident_id}"),
        target=Target("k8s_deployment", "staging/checkout-api", "staging"),
    )

    with httpx.Client(timeout=30) as c:
        logs = c.get(f"{BASE}/metrics").text
    investigate = recorder.emit(
        action_type="investigate",
        summary="Scripted diagnosis of elevated 5xx on staging/checkout-api.",
        outcome_status="success",
        outcome_details=SCENARIO["root_cause"],
        evidence=[
            recorder.capture(evidence_type="http_probe",
                             source="POST /checkout (reproduction attempt)",
                             content=json.dumps({"status_code": before.status_code,
                                                 "body": before.text[:400]}, indent=2),
                             content_type="application/json"),
            recorder.capture(evidence_type="metric_query", source="GET /metrics", content=logs),
        ],
    )
    print(f"  2. investigate recorded {D}{investigate.record_id[:8]}{O}")

    propose = recorder.emit(
        action_type="propose_fix",
        summary=f"Proposed: {SCENARIO['fix']['description']}",
        outcome_status="pending",
        outcome_details="Proposal recorded. No change applied; awaiting human approval.",
        evidence=[recorder.capture(
            evidence_type="diff",
            source="proposed remediation",
            content=json.dumps({**SCENARIO["fix"], "root_cause": SCENARIO["root_cause"]}, indent=2),
            content_type="application/json")],
        proposed_action={k: SCENARIO["fix"][k] for k in
                         ("description", "change_type", "command_or_diff",
                          "reversible", "blast_radius")},
        in_response_to=investigate.record_id,
    )
    print(f"  3. propose_fix recorded {D}{propose.record_id[:8]}{O}")

    gate = ApprovalGate()
    request_id = gate.request_approval(incident_id=incident_id,
                                       propose_record_id=propose.record_id)
    print(f"  4. approval requested {D}{request_id[:8]}{O} "
          f"({len(gate.pending())} pending)")

    with httpx.Client(timeout=30) as c:
        still = c.post(f"{BASE}/checkout", json={"digital_only": True})
    assert still.status_code == 500, "nothing should have changed before approval"
    print(f"  5. {G}nothing executed while pending{O} — /checkout still {still.status_code}")

    approve_id = gate.decide(
        request_record_id=request_id, approve=True,
        note="Scripted pipeline check. Reversible staging rollback; approved to exercise the executor.",
        channel="trial_harness_unattended",
    )
    print(f"  6. approved {D}{approve_id[:8]}{O}")

    result = Executor(seedapp_base=BASE).execute(approve_id)
    colour = G if result.success else R
    print(f"  7. {colour}executed: {result.detail}{O} ({result.duration_ms}ms)")

    with httpx.Client(timeout=30) as c:
        after = c.post(f"{BASE}/checkout", json={"digital_only": True})
        state = c.get(f"{BASE}/admin/state").json()
    print(f"  8. /checkout now {after.status_code}, deployed version {state['version']}")

    from scripts.verify_chain import verify
    report = verify()
    ok = report.ok and after.status_code == 201 and result.success
    print(f"\n  chain: {G if report.ok else R}{report.verified}/{report.total} verified{O}")
    print(f"  {G + 'PIPELINE OK' + O if ok else R + 'PIPELINE FAILED' + O}")
    print(f"  {D}view it: http://127.0.0.1:8090/incident/{incident_id}{O}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
