#!/usr/bin/env python3
"""Run one incident end to end: inject, investigate, propose, approve, execute.

    python scripts/run_incident.py --fault bad_deploy
    python scripts/run_incident.py --fault slow_query --interactive

The loop deliberately generates real traffic against the service before the
investigation starts. Without it the alert would be a claim rather than a fact,
and the agent would be diagnosing a fault that had never actually manifested in
a log line or a metric. Injecting a fault and then immediately asking about it
is how integrations come to depend on symptoms that real incidents do not have.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from adapter.holmes_adapter import HolmesAdapter  # noqa: E402
from approval.executor import Executor  # noqa: E402
from approval.gate import ApprovalGate  # noqa: E402

G, R, Y, D, B, C, O = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[36m", "\033[0m"
)

# What the on-call engineer would actually be paged with. Deliberately states
# the symptom and not the cause: an alert that names the root cause is not an
# investigation, it is a lookup.
ALERTS = {
    "bad_deploy": (
        "PagerDuty alert CHECKOUT-5XX: the HTTP 5xx ratio on staging/checkout-api has "
        "exceeded 20% for the last 5 minutes. POST /checkout appears to be the affected "
        "route. Find the root cause and propose a remediation."
    ),
    "pool_exhaustion": (
        "PagerDuty alert CHECKOUT-AVAIL: GET /orders on staging/checkout-api is returning "
        "HTTP 503 for a significant fraction of requests under normal traffic levels. "
        "Find the root cause and propose a remediation."
    ),
    "slow_query": (
        "PagerDuty alert CHECKOUT-LATENCY: p95 latency for GET /reports/revenue on "
        "staging/checkout-api has breached its 1s SLO and is now above 2s. "
        "Find the root cause and propose a remediation."
    ),
    "leaked_secret": (
        "Security alert CHECKOUT-SECRET: an automated credential scan flagged a possible "
        "hardcoded cloud access key in the checkout-api repository. Confirm whether a live "
        "credential is exposed, identify where it was introduced, and propose a remediation."
    ),
}

# Traffic that makes each fault genuinely manifest before anyone looks.
TRAFFIC = {
    "bad_deploy": [("POST", "/checkout", {"digital_only": True})] * 8,
    "pool_exhaustion": [("GET", "/orders", None)] * 6,
    "slow_query": [("GET", "/reports/revenue", None)] * 2,
    "leaked_secret": [("GET", "/health", None)] * 2,
}

EXPECTED_REMEDIATION = {
    "bad_deploy": "rollback_deploy",
    "pool_exhaustion": "increase_pool_size",
    "slow_query": "restore_revenue_index",
    "leaked_secret": "rotate_credential",
}


def admin(base: str, path: str, body: dict | None = None, timeout: float = 30.0) -> dict:
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()


def generate_traffic(base: str, fault: str) -> int:
    """Drive real requests so the fault shows up in logs and metrics."""
    requests = TRAFFIC.get(fault, [("GET", "/health", None)])

    def one(spec):
        method, path, body = spec
        try:
            with httpx.Client(timeout=130.0) as client:
                resp = (client.post(base + path, json=body) if method == "POST"
                        else client.get(base + path))
                return resp.status_code
        except Exception:
            return 0

    # Concurrently, because pool exhaustion only manifests under concurrency --
    # a serial client would never see it.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(requests)) as pool:
        codes = list(pool.map(one, requests))
    return sum(1 for c in codes if c >= 500 or c == 0)


def run(args) -> dict:
    base = args.seedapp_base.rstrip("/")
    incident_id = args.incident_id or (
        f"INC-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    )
    outcome: dict = {
        "incident_id": incident_id, "fault": args.fault,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expected_remediation": EXPECTED_REMEDIATION.get(args.fault),
    }

    print(f"\n{B}Incident {incident_id}{O}  {D}fault={args.fault}{O}")

    # 1. Reset, then inject.
    if not args.no_reset:
        admin(base, "/admin/reset")
    admin(base, "/admin/inject", {"mode": args.fault})
    print(f"  {D}1. injected {args.fault}{O}")

    # 2. Make it manifest.
    errors = generate_traffic(base, args.fault)
    print(f"  {D}2. generated traffic ({errors} failing request(s)){O}")

    # 3. Investigate.
    print(f"  {D}3. HolmesGPT investigating...{O}", flush=True)
    t0 = time.perf_counter()
    adapter = HolmesAdapter(seedapp_base=base, model=args.model)
    inv = adapter.investigate(
        incident_id=incident_id,
        alert=ALERTS[args.fault],
        environment="staging",
        resource_id="staging/checkout-api",
    )
    outcome["investigation"] = {
        "ok": inv.ok,
        "error": inv.error,
        "tool_calls": len(inv.tool_invocations),
        "tools_used": [c.tool_name for c in inv.tool_invocations],
        "duration_ms": inv.duration_ms,
        "cost_usd": inv.llm_cost_usd,
        "tokens": inv.total_tokens,
        "step_record_ids": inv.step_record_ids,
        "conclusion_record_id": inv.conclusion_record_id,
        "propose_record_id": inv.propose_record_id,
    }
    if inv.conclusion:
        outcome["investigation"].update({
            "failure_mode": inv.conclusion.failure_mode,
            "confidence": inv.conclusion.confidence,
            "root_cause": inv.conclusion.root_cause,
        })

    if not inv.ok:
        print(f"  {R}investigation failed: {inv.error}{O}")
        outcome["result"] = "investigation_failed"
        outcome["stage_reached"] = "investigate"
        return outcome

    c = inv.conclusion
    print(f"     {G}diagnosed{O} {c.failure_mode} ({c.confidence} confidence) "
          f"in {len(inv.tool_invocations)} tool call(s), {inv.duration_ms // 1000}s")
    outcome["diagnosis_correct"] = c.failure_mode == args.fault

    if not inv.propose_record_id:
        print(f"  {Y}no remediation proposed{O}")
        outcome["result"] = "no_proposal"
        outcome["stage_reached"] = "investigate"
        return outcome
    if inv.proposed_action is None:
        print(f"  {Y}proposal was refused by policy: {inv.error}{O}")
        outcome["result"] = "proposal_refused_by_policy"
        outcome["stage_reached"] = "propose_fix"
        return outcome

    proposed = inv.proposed_action["remediation_action"]
    outcome["proposed_remediation"] = proposed
    outcome["remediation_correct"] = proposed == EXPECTED_REMEDIATION.get(args.fault)
    print(f"  {D}4. proposed {proposed}{O}")

    # 5. Request approval.
    gate = ApprovalGate()
    request_id = gate.request_approval(
        incident_id=incident_id, propose_record_id=inv.propose_record_id
    )
    outcome["request_record_id"] = request_id
    print(f"  {D}5. approval requested ({request_id[:8]}){O}")
    outcome["stage_reached"] = "request_approval"

    if args.interactive:
        print(f"\n  {Y}Waiting for a human decision. In another shell:{O}")
        print(f"    python -m approval.cli show {request_id[:8]}")
        print(f"    python -m approval.cli approve {request_id[:8]} --note \"...\"\n")
        outcome["result"] = "awaiting_human_approval"
        return outcome

    # 6. Approve. Unattended approvals are labelled as such in the ledger so
    # that an auditor can separate them from genuine per-incident review.
    pending = gate.get_pending(request_id)
    if pending.needs_override and not args.allow_override:
        print(f"  {Y}policy refuses this change; not auto-approving{O}")
        outcome["result"] = "blocked_by_policy"
        return outcome

    approve_id = gate.decide(
        request_record_id=request_id,
        approve=True,
        note=(
            "Approved automatically by the unattended trial harness under standing "
            "pre-authorisation for reversible staging remediations. This was NOT a "
            "per-incident human review; see approval_channel."
        ),
        channel="trial_harness_unattended",
        override=pending.needs_override,
    )
    outcome["approve_record_id"] = approve_id
    outcome["stage_reached"] = "approve"
    print(f"  {D}6. approved ({approve_id[:8]}, unattended){O}")

    # 7. Execute.
    result = Executor(seedapp_base=base).execute(approve_id)
    outcome["execute_record_id"] = result.execute_record_id
    outcome["execution_success"] = result.success
    outcome["execution_detail"] = result.detail
    outcome["stage_reached"] = "execute"
    colour = G if result.success else R
    print(f"  {colour}7. executed: {result.detail}{O}")

    outcome["result"] = "success" if result.success else "execution_failed"
    outcome["total_ms"] = int((time.perf_counter() - t0) * 1000)
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fault", required=True, choices=sorted(ALERTS))
    ap.add_argument("--incident-id", default=None)
    ap.add_argument("--seedapp-base", default="http://127.0.0.1:8081")
    ap.add_argument("--model", default=None)
    ap.add_argument("--interactive", action="store_true",
                    help="stop at the approval gate and wait for a real human")
    ap.add_argument("--allow-override", action="store_true",
                    help="let the unattended approver use a human override")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print(f"\n{R}No LLM API key found.{O} HolmesGPT cannot investigate without one.")
        print(f"{D}Copy .env.example to .env and set ANTHROPIC_API_KEY.{O}\n")
        return 2

    outcome = run(args)
    if args.json:
        print(json.dumps(outcome, indent=2, default=str))
    print()
    return 0 if outcome.get("result") in ("success", "awaiting_human_approval") else 1


if __name__ == "__main__":
    raise SystemExit(main())
