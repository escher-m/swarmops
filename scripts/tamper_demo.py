#!/usr/bin/env python3
"""Deliberately corrupt the ledger and show the verifier catching it.

The append-only controls in 002_roles.sql stop the application and the table
owner. They cannot stop someone who reaches the database with superuser rights
and turns the triggers off -- nothing enforced inside the database can. That is
exactly the gap the hash chain exists to close, and this script demonstrates it
by playing the attacker: it connects as superuser, disables the protection,
makes a change, and puts the protection back.

Three attacks are staged, because they fail in different ways:

  edit_record     Change the outcome of an executed action from failure to
                  success -- the single most valuable edit an attacker could
                  make against an audit ledger.
  delete_record   Remove an approval record entirely, to make an unapproved
                  execution look authorized.
  swap_evidence   Leave the ledger untouched and instead rewrite the log
                  excerpt the agent relied on, so the record's justification
                  silently becomes something else.

Each is applied, detected, and then reverted, so the ledger is left exactly as
it was found. Run scripts/verify_chain.py afterwards to confirm that.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from ledger.store import LedgerStore  # noqa: E402
from scripts.verify_chain import verify  # noqa: E402

# Superuser DSN: the attacker's position, not the application's.
SUPERUSER_DSN = f"host={ROOT / '.pgdata' / 'sockets'} port=5433 user=postgres dbname=swarmops_ledger"

G, R, Y, D, B, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


def _with_triggers_off(conn, fn):
    """Run fn with the append-only triggers disabled, then restore them."""
    conn.execute("ALTER TABLE agent_action_records DISABLE TRIGGER USER")
    try:
        return fn()
    finally:
        conn.execute("ALTER TABLE agent_action_records ENABLE TRIGGER USER")


def report_line(label: str) -> None:
    print(f"\n{B}{label}{O}")


def show_verify(expect_broken: bool) -> bool:
    rep = verify()
    if rep.ok:
        print(f"  verifier says: {G}✅ {rep.verified}/{rep.total} records verified, no gaps detected{O}")
    else:
        print(f"  verifier says: {R}❌ {rep.verified}/{rep.total} verified, "
              f"{len(rep.findings)} problem(s):{O}")
        for f in rep.findings[:6]:
            print(f"    {R}{f.kind}{O} at seq {f.seq}")
            print(f"      {D}{f.detail}{O}")
        if len(rep.findings) > 6:
            print(f"    {D}... and {len(rep.findings) - 6} more{O}")
    caught = not rep.ok
    ok = caught == expect_broken
    verdict = f"{G}DETECTED{O}" if caught else f"{R}NOT DETECTED{O}"
    if expect_broken:
        print(f"  outcome: tampering {verdict}")
    return ok


def attack_edit_record(conn) -> bool:
    report_line("Attack 1 — rewrite history: flip an execution's outcome to success")
    row = conn.execute(
        "SELECT seq, record FROM agent_action_records WHERE action_type = 'execute' "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if not row:
        print(f"  {Y}no execute record in the ledger; skipping{O}")
        return True
    seq, original = row[0], row[1]
    tampered = json.loads(json.dumps(original))
    tampered["outcome"]["details"] = (
        "Rollout completed cleanly with no impact. Nothing to see here."
    )
    tampered["summary"] = "Routine maintenance completed successfully."
    print(f"  {D}editing seq {seq}: summary and outcome.details rewritten, "
          f"record_hash left as-is{O}")

    _with_triggers_off(conn, lambda: conn.execute(
        "UPDATE agent_action_records SET record = %s WHERE seq = %s", (json.dumps(tampered), seq)))
    conn.commit()
    ok = show_verify(expect_broken=True)

    _with_triggers_off(conn, lambda: conn.execute(
        "UPDATE agent_action_records SET record = %s WHERE seq = %s", (json.dumps(original), seq)))
    conn.commit()
    return ok


def attack_delete_record(conn) -> bool:
    report_line("Attack 2 — remove the approval record, to make an execution look authorized")
    row = conn.execute(
        "SELECT seq, record FROM agent_action_records WHERE action_type = 'approve' "
        "ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    if not row:
        print(f"  {Y}no approve record in the ledger; skipping{O}")
        return True
    seq, original = row[0], row[1]
    print(f"  {D}deleting seq {seq} ({original['action_type']} by "
          f"{original['approver']['approver_id']}){O}")

    _with_triggers_off(conn, lambda: conn.execute(
        "DELETE FROM agent_action_records WHERE seq = %s", (seq,)))
    conn.commit()
    ok = show_verify(expect_broken=True)

    # Restoring needs the original seq back, so the identity column is
    # overridden explicitly rather than letting the sequence assign a new one.
    _with_triggers_off(conn, lambda: conn.execute(
        "INSERT INTO agent_action_records (seq, record, event_time) VALUES (%s, %s, %s)",
        (seq, json.dumps(original), original["timestamp"])))
    conn.commit()
    return ok


def attack_swap_evidence() -> bool:
    report_line("Attack 3 — leave the ledger alone, rewrite the evidence it points at")
    store = LedgerStore()
    blob_path = None
    for row in store.iter_chain():
        for item in row["record"].get("evidence") or []:
            ref = item.get("snapshot_ref_or_inline") or {}
            if ref.get("kind") == "blob":
                digest = ref["sha256"].removeprefix("sha256:")
                blob_path = ROOT / "evidence_store" / digest[:2] / digest
                cited_by = row["seq"]
                break
        if blob_path:
            break
    if not blob_path or not blob_path.exists():
        print(f"  {Y}no evidence blob in the ledger; skipping{O}")
        return True

    backup = blob_path.with_suffix(".orig")
    shutil.copy2(blob_path, backup)
    print(f"  {D}rewriting the log excerpt cited by seq {cited_by} "
          f"({blob_path.name[:16]}...){O}")
    blob_path.chmod(0o644)
    blob_path.write_text("2026-09-18T14:10:00Z checkout-api INFO all systems nominal\n")

    ok = show_verify(expect_broken=True)

    shutil.move(str(backup), str(blob_path))
    blob_path.chmod(0o444)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--attack", choices=["edit", "delete", "evidence", "all"], default="all")
    args = ap.parse_args()

    print(f"\n{B}Tamper-detection demonstration{O}")
    print(f"{D}Playing the attacker: superuser database access, protections disabled.{O}")

    report_line("Baseline — the ledger as it stands")
    if not show_verify(expect_broken=False):
        print(f"\n{R}The ledger does not verify before the demo even starts. "
              f"Fix that first.{O}\n")
        return 1

    results = []
    with psycopg.connect(SUPERUSER_DSN) as conn:
        if args.attack in ("edit", "all"):
            results.append(("edit_record", attack_edit_record(conn)))
        if args.attack in ("delete", "all"):
            results.append(("delete_record", attack_delete_record(conn)))
    if args.attack in ("evidence", "all"):
        results.append(("swap_evidence", attack_swap_evidence()))

    report_line("Restored — the ledger after every change was reverted")
    restored = show_verify(expect_broken=False)

    print(f"\n{B}Result{O}")
    for name, ok in results:
        print(f"  {G + 'detected' + O if ok else R + 'MISSED' + O:<22} {name}")
    print(f"  {G + 'clean' + O if restored else R + 'DIRTY' + O:<22} ledger restored")

    all_ok = all(ok for _, ok in results) and restored
    if all_ok:
        print(f"\n{G}Every tampering attempt was detected, and the ledger was left as found.{O}\n")
    else:
        print(f"\n{R}At least one tampering attempt went undetected.{O}\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
