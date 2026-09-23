#!/usr/bin/env python3
"""Walk the ledger and prove nothing has been tampered with.

This is the artifact you hand to someone who does not trust you. It re-derives
every hash from the stored content, checks that each record commits to its
predecessor, and re-hashes every evidence blob against the address the record
cites. It reads the database directly and shares no state with the write path,
so a bug in the writer cannot hide from it.

Five independent things are checked, because each catches a different attack:

  hash integrity   Each record's stored record_hash is recomputed from its
                   content. Catches edits to any field.
  chain linkage    Each record's prev_record_hash equals its predecessor's
                   record_hash. Catches deletion and reordering -- the two
                   things per-record hashing alone would miss.
  sequence gaps    seq is dense and ascending. A gap means a row was removed by
                   someone who could also fix the links, so it is reported
                   separately rather than folded into linkage.
  schema validity  Every record still satisfies the published schema.
  evidence         Every referenced blob exists and still hashes to its address.
                   Catches swapping the log excerpt that justified an action
                   while leaving the record untouched.

Exit status: 0 when the ledger verifies, 1 when it does not.

    python scripts/verify_chain.py            # human-readable report
    python scripts/verify_chain.py --json     # machine-readable, for CI
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ledger.canonical import GENESIS_HASH, compute_record_hash  # noqa: E402
from ledger.evidence_store import EvidenceStore, default_store  # noqa: E402
from ledger.store import LedgerStore  # noqa: E402
from ledger.validation import validation_errors  # noqa: E402


@dataclass
class Finding:
    seq: int | None
    record_id: str | None
    kind: str
    detail: str

    def as_dict(self) -> dict:
        return {"seq": self.seq, "record_id": self.record_id, "kind": self.kind, "detail": self.detail}


@dataclass
class Report:
    total: int = 0
    verified: int = 0
    evidence_checked: int = 0
    findings: list[Finding] = field(default_factory=list)
    head_hash: str | None = None
    first_seq: int | None = None
    last_seq: int | None = None

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "total_records": self.total,
            "verified_records": self.verified,
            "evidence_artifacts_checked": self.evidence_checked,
            "head_hash": self.head_hash,
            "seq_range": [self.first_seq, self.last_seq],
            "findings": [f.as_dict() for f in self.findings],
        }


def verify(
    store: LedgerStore | None = None,
    evidence: EvidenceStore | None = None,
    *,
    check_schema: bool = True,
    check_evidence: bool = True,
) -> Report:
    store = store or LedgerStore()
    evidence = evidence or default_store()
    report = Report()

    expected_prev = GENESIS_HASH
    expected_seq: int | None = None

    for row in store.iter_chain():
        seq = row["seq"]
        record = row["record"]
        rid = record.get("record_id")
        report.total += 1
        if report.first_seq is None:
            report.first_seq = seq
        report.last_seq = seq

        record_ok = True

        # 1. Does the record's stored hash still match its content?
        try:
            recomputed = compute_record_hash(record)
        except Exception as exc:  # canonicalization failure is itself a finding
            report.findings.append(Finding(seq, rid, "uncanonicalizable", str(exc)))
            record_ok = False
            recomputed = None

        stored = record.get("record_hash")
        if recomputed is not None and stored != recomputed:
            report.findings.append(
                Finding(
                    seq, rid, "hash_mismatch",
                    f"record content does not match its hash "
                    f"(stored {stored[:16] if stored else 'None'}..., recomputed {recomputed[:16]}...) "
                    f"-- this record has been altered after it was written",
                )
            )
            record_ok = False

        # The column and the payload are the same field via a generated column,
        # but check anyway: it is cheap, and if it ever diverges the storage
        # layer is broken in a way worth shouting about.
        if stored != row["record_hash"]:
            report.findings.append(
                Finding(seq, rid, "column_payload_divergence",
                        "record_hash column disagrees with the record payload")
            )
            record_ok = False

        # 2. Does this record point at its actual predecessor?
        actual_prev = record.get("prev_record_hash")
        if actual_prev != expected_prev:
            if expected_prev == GENESIS_HASH:
                detail = (
                    f"first record does not start the chain: prev_record_hash is "
                    f"{actual_prev[:16] if actual_prev else 'None'}..., expected 64 zeros "
                    f"-- one or more records before this one have been removed"
                )
            else:
                detail = (
                    f"broken link: prev_record_hash is {actual_prev[:16] if actual_prev else 'None'}..., "
                    f"but the preceding record hashes to {expected_prev[:16]}... "
                    f"-- a record was removed, reordered, or altered here"
                )
            report.findings.append(Finding(seq, rid, "chain_break", detail))
            record_ok = False

        # 3. Is the sequence dense?
        if expected_seq is not None and seq != expected_seq:
            report.findings.append(
                Finding(seq, rid, "sequence_gap",
                        f"expected seq {expected_seq}, found {seq} -- "
                        f"{seq - expected_seq} record(s) missing from the ledger")
            )
            record_ok = False
        expected_seq = seq + 1

        # 4. Is it still a valid record?
        if check_schema:
            for err in validation_errors(record):
                report.findings.append(Finding(seq, rid, "schema_violation", err))
                record_ok = False

        # 5. Is the evidence it cites still the evidence it cited?
        if check_evidence:
            for i, item in enumerate(record.get("evidence") or []):
                ref = item.get("snapshot_ref_or_inline") or {}
                if ref.get("kind") != "blob":
                    continue
                report.evidence_checked += 1
                digest = (ref.get("sha256") or "").removeprefix("sha256:")
                try:
                    data = evidence.get(digest)
                except FileNotFoundError:
                    report.findings.append(
                        Finding(seq, rid, "evidence_missing",
                                f"evidence[{i}] ({item.get('evidence_type')}) cites blob "
                                f"{digest[:16]}... which is not in the store -- "
                                f"the justification for this action can no longer be produced")
                    )
                    record_ok = False
                    continue
                except Exception as exc:
                    report.findings.append(
                        Finding(seq, rid, "evidence_tampered",
                                f"evidence[{i}]: {exc}")
                    )
                    record_ok = False
                    continue
                if ref.get("size_bytes") != len(data):
                    report.findings.append(
                        Finding(seq, rid, "evidence_size_mismatch",
                                f"evidence[{i}] blob is {len(data)} bytes, record says "
                                f"{ref.get('size_bytes')}")
                    )
                    record_ok = False

        # Records with inline evidence need no separate check: record_hash
        # already covers the inline content, so step 1 caught any edit.

        if record_ok:
            report.verified += 1
        expected_prev = stored or ""

    report.head_hash = expected_prev if report.total else None
    return report


# --------------------------------------------------------------------- output

G, R, Y, D, B, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


def print_report(report: Report, colour: bool = True) -> None:
    g, r, y, d, b, o = (G, R, Y, D, B, O) if colour else ("",) * 6

    print(f"\n{b}Ledger hash-chain verification{o}\n")
    if report.total == 0:
        print(f"  {y}ledger is empty -- nothing to verify{o}\n")
        return

    print(f"  records         {report.total}")
    print(f"  seq range       {report.first_seq}..{report.last_seq}")
    print(f"  evidence blobs  {report.evidence_checked} re-hashed against their addresses")
    print(f"  chain head      {report.head_hash}")
    print()

    if report.ok:
        print(f"  {g}{b}✅ {report.verified}/{report.total} records verified, no gaps detected{o}")
        print(f"  {d}every hash recomputed from content, every link followed, "
              f"every blob re-hashed{o}\n")
        return

    print(f"  {r}{b}❌ {report.verified}/{report.total} records verified — "
          f"{len(report.findings)} problem(s) found{o}\n")
    by_kind: dict[str, list[Finding]] = {}
    for f in report.findings:
        by_kind.setdefault(f.kind, []).append(f)
    for kind, items in by_kind.items():
        print(f"  {r}{kind}{o} ({len(items)})")
        for f in items[:10]:
            print(f"    seq {f.seq} {d}{f.record_id}{o}")
            print(f"      {f.detail}")
        if len(items) > 10:
            print(f"    {d}... and {len(items) - 10} more{o}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the ledger hash chain.")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--no-schema", action="store_true", help="skip schema revalidation")
    ap.add_argument("--no-evidence", action="store_true", help="skip evidence blob re-hashing")
    ap.add_argument("--dsn", default=None, help="Postgres DSN (defaults to the local dev cluster)")
    args = ap.parse_args()

    report = verify(
        LedgerStore(args.dsn),
        check_schema=not args.no_schema,
        check_evidence=not args.no_evidence,
    )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print_report(report, colour=sys.stdout.isatty())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
