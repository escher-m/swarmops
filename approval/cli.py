"""Command-line approval queue.

    python -m approval.cli list
    python -m approval.cli show <request-id>
    python -m approval.cli approve <request-id> --note "why"
    python -m approval.cli reject  <request-id> --note "why"

The dashboard offers the same three actions over HTTP and writes identical
records; this CLI exists because an approval path that needs a browser is a
liability during an incident at 3am.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap

from approval.executor import Executor
from approval.gate import ApprovalGate, current_approver_id

G, R, Y, D, B, C, O = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[36m", "\033[0m"
)


def cmd_list(gate: ApprovalGate, args) -> int:
    items = gate.pending()
    if not items:
        print(f"\n  {D}no approvals pending{O}\n")
        return 0
    print(f"\n{B}{len(items)} approval(s) pending{O}\n")
    for item in items:
        pa = item.proposed_action
        flag = f"  {R}[needs override]{O}" if item.needs_override else ""
        print(f"  {C}{item.request_record_id[:8]}{O}  {item.incident_id}{flag}")
        print(f"    {pa.get('description', item.summary)[:110]}")
        print(f"    {D}{pa.get('change_type')} on {item.target['resource_id']} "
              f"({item.target['environment']}) · reversible={pa.get('reversible')} "
              f"· blast={pa.get('blast_radius')} · requested {item.requested_at}{O}")
        print()
    print(f"  {D}approve with: python -m approval.cli approve <id> --note \"...\"{O}\n")
    return 0


def cmd_show(gate: ApprovalGate, args) -> int:
    item = gate.get_pending(args.request_id)
    pa = item.proposed_action
    print(f"\n{B}Approval request {item.request_record_id}{O}")
    print(f"{D}incident {item.incident_id} · requested {item.requested_at}{O}\n")
    print(f"{B}Root cause{O}")
    print(textwrap.indent(textwrap.fill(item.root_cause or "(not recorded)", 76), "  "))
    print(f"\n{B}Proposed change{O}")
    print(textwrap.indent(textwrap.fill(pa.get("description", ""), 76), "  "))
    print(f"\n{B}Command to be applied{O}")
    print(textwrap.indent(pa.get("command_or_diff", "(none)"), "  "))
    print(f"\n{B}Risk{O}")
    print(f"  change_type   {pa.get('change_type')}")
    print(f"  reversible    {pa.get('reversible')}")
    print(f"  blast radius  {pa.get('blast_radius')}")
    print(f"  environment   {item.target['environment']}")
    print(f"  resource      {item.target['resource_id']}")
    print(f"\n{B}Policy{O}")
    print(f"  {item.policy_reference['allowed_by']} ({item.policy_reference['policy_id']})")
    if item.needs_override:
        print(f"\n  {R}Policy does not permit executing this change. Approving it "
              f"requires --override.{O}")
    print(f"\n{B}Evidence{O}\n  {item.evidence_count} artifact(s) on the proposal record "
          f"{item.propose_record_id}\n")
    return 0


def cmd_decide(gate: ApprovalGate, args, approve: bool) -> int:
    item = gate.get_pending(args.request_id)
    if approve and item.needs_override and not args.override:
        print(f"\n{R}Refusing: policy does not permit executing this change.{O}")
        print(f"{D}Re-run with --override to approve it as a named human override.{O}\n")
        return 2
    try:
        record_id = gate.decide(
            request_record_id=item.request_record_id,
            approve=approve,
            note=args.note,
            channel=args.channel,
            override=args.override,
        )
    except ValueError as exc:
        print(f"\n{R}{exc}{O}\n")
        return 2

    verb = "approved" if approve else "rejected"
    print(f"\n  {G if approve else Y}{verb}{O} by {current_approver_id()}")
    print(f"  {D}decision recorded as {record_id}{O}")

    if not approve:
        print()
        return 0

    if args.no_execute:
        print(f"  {D}--no-execute given; not applying the change{O}\n")
        return 0

    print(f"\n  applying the approved remediation...")
    result = Executor(seedapp_base=args.seedapp_base).execute(record_id)
    colour = G if result.success else R
    print(f"  {colour}{result.detail}{O}")
    print(f"  {D}execution recorded as {result.execute_record_id} ({result.duration_ms}ms){O}\n")
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="approval.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seedapp-base", default="http://127.0.0.1:8081")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show pending approvals")

    p_show = sub.add_parser("show", help="show one pending approval in full")
    p_show.add_argument("request_id")

    for name, helptext in (("approve", "approve and apply"), ("reject", "reject")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("request_id")
        p.add_argument("--note", required=True, help="why you decided this (recorded in the ledger)")
        p.add_argument("--channel", default="cli",
                       help="where the decision was made; use 'trial_harness_unattended' "
                            "for automated runs so attended and unattended approvals "
                            "stay distinguishable in the ledger")
        p.add_argument("--override", action="store_true",
                       help="approve as a named human override when policy would refuse")
        p.add_argument("--no-execute", action="store_true",
                       help="record the decision without applying the change")

    args = ap.parse_args(argv)
    gate = ApprovalGate()
    if args.cmd == "list":
        return cmd_list(gate, args)
    if args.cmd == "show":
        return cmd_show(gate, args)
    try:
        return cmd_decide(gate, args, approve=args.cmd == "approve")
    except LookupError as exc:
        print(f"\n{R}{exc}{O}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
