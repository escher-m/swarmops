#!/usr/bin/env python3
"""Run the trial loop and report honestly on where it breaks.

    python scripts/run_trials.py --runs 20

The point of this script is not to produce a good number. It is to produce a
true one. Every run is recorded with the stage it reached, so a partial failure
("diagnosed correctly but proposed the wrong remediation") is visible as such
rather than collapsing into a single pass/fail. The per-stage breakdown is what
tells you whether the weak link is the agent, the schema, the policy or the
executor -- and that is the number worth showing a design partner.

Runs rotate across all four failure modes so no single mode dominates the
result, and each run resets the service first so runs cannot contaminate one
another.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import os  # noqa: E402

from scripts.run_incident import ALERTS, run  # noqa: E402

G, R, Y, D, B, C, O = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[36m", "\033[0m"
)

FAULTS = sorted(ALERTS)

# Ordered; a run is credited with the furthest stage it reached.
STAGES = ["investigate", "propose_fix", "request_approval", "approve", "execute"]


class Args:
    """Stands in for the argparse namespace run() expects."""

    def __init__(self, fault: str, base: str, model: str | None):
        self.fault = fault
        self.incident_id = None
        self.seedapp_base = base
        self.model = model
        self.interactive = False
        self.allow_override = False
        self.no_reset = False
        self.json = False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seedapp-base", default="http://127.0.0.1:8081")
    ap.add_argument("--model", default=None)
    ap.add_argument("--faults", default=",".join(FAULTS),
                    help="comma-separated subset of failure modes to rotate through")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print(f"\n{R}No LLM API key found.{O} Set ANTHROPIC_API_KEY in .env first.\n")
        return 2

    faults = [f.strip() for f in args.faults.split(",") if f.strip()]
    for f in faults:
        if f not in ALERTS:
            print(f"{R}unknown fault {f!r}{O}"); return 2

    out_dir = ROOT / "runs"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else out_dir / f"trials-{stamp}.json"

    print(f"\n{B}Trial loop — {args.runs} runs across {len(faults)} failure mode(s){O}")
    print(f"{D}results will be written to {out_path.relative_to(ROOT)}{O}")

    results: list[dict] = []
    started = time.perf_counter()

    for i in range(args.runs):
        fault = faults[i % len(faults)]
        print(f"\n{C}── run {i + 1}/{args.runs} ──{O}", end="")
        try:
            outcome = run(Args(fault, args.seedapp_base, args.model))
        except KeyboardInterrupt:
            print(f"\n{Y}interrupted after {i} run(s){O}")
            break
        except Exception as exc:
            # A crash is a result too, and hiding it would flatter the numbers.
            outcome = {
                "incident_id": None, "fault": fault, "result": "harness_error",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
                "stage_reached": None,
            }
            print(f"  {R}harness error: {exc}{O}")
        outcome["run_index"] = i + 1
        results.append(outcome)
        _write(out_path, results, args, started)

    _report(results, time.perf_counter() - started, out_path)
    return 0


def _write(path: Path, results: list[dict], args, started: float) -> None:
    """Persist after every run so an interrupted loop still yields data."""
    path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_runs": args.runs,
        "completed_runs": len(results),
        "model": args.model or os.environ.get("HOLMES_MODEL", "anthropic/claude-sonnet-5"),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "runs": results,
    }, indent=2, default=str))


def _report(results: list[dict], elapsed: float, out_path: Path) -> None:
    if not results:
        print(f"\n{Y}no runs completed{O}\n"); return

    n = len(results)
    full = [r for r in results if r.get("result") == "success"]
    diagnosed = [r for r in results if r.get("diagnosis_correct")]
    right_fix = [r for r in results if r.get("remediation_correct")]

    stage_counts = Counter(r.get("stage_reached") for r in results)
    reached = {s: sum(stage_counts[x] for x in STAGES[STAGES.index(s):]) for s in STAGES}

    by_fault: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_fault[r["fault"]].append(r)

    print(f"\n\n{B}{'=' * 74}{O}")
    print(f"{B}Trial results — {n} run(s) in {elapsed / 60:.1f} min{O}")
    print(f"{B}{'=' * 74}{O}\n")

    def pct(k: int) -> str:
        p = 100 * k / n
        colour = G if p >= 80 else Y if p >= 50 else R
        return f"{colour}{k:>3}/{n}  {p:5.1f}%{O}"

    print(f"{B}Overall{O}")
    print(f"  full chain completed and verified   {pct(len(full))}")
    print(f"  root cause identified correctly     {pct(len(diagnosed))}")
    print(f"  correct remediation proposed        {pct(len(right_fix))}")

    print(f"\n{B}How far each run got{O}  {D}(cumulative — a run that executed also proposed){O}")
    for s in STAGES:
        bar = "█" * int(20 * reached[s] / n)
        print(f"  {s:<18} {reached[s]:>3}/{n}  {D}{bar}{O}")

    print(f"\n{B}By failure mode{O}")
    print(f"  {'mode':<18} {'runs':>4} {'diagnosed':>10} {'right fix':>10} {'completed':>10}")
    for fault in sorted(by_fault):
        rs = by_fault[fault]
        d = sum(1 for r in rs if r.get("diagnosis_correct"))
        f_ = sum(1 for r in rs if r.get("remediation_correct"))
        c = sum(1 for r in rs if r.get("result") == "success")
        print(f"  {fault:<18} {len(rs):>4} {d:>10} {f_:>10} {c:>10}")

    failures = [r for r in results if r.get("result") != "success"]
    if failures:
        print(f"\n{B}Where it broke{O}  {D}({len(failures)} run(s) did not complete){O}")
        for reason, count in Counter(r.get("result") for r in failures).most_common():
            print(f"  {R}{reason:<28}{O} {count}")
        print(f"\n{D}  first three failures in detail:{O}")
        for r in failures[:3]:
            print(f"    run {r['run_index']} ({r['fault']}): {r.get('result')}")
            detail = r.get("error") or r.get("execution_detail") or ""
            if detail:
                print(f"      {D}{str(detail)[:200]}{O}")

    costs = [r["investigation"]["cost_usd"] for r in results
             if r.get("investigation", {}).get("cost_usd")]
    durs = [r["investigation"]["duration_ms"] for r in results
            if r.get("investigation", {}).get("duration_ms")]
    calls = [r["investigation"]["tool_calls"] for r in results if r.get("investigation")]
    if durs:
        print(f"\n{B}Cost and latency per investigation{O}")
        print(f"  median duration   {statistics.median(durs) / 1000:.1f}s")
        print(f"  median tool calls {statistics.median(calls):.0f}")
        if costs:
            print(f"  median cost       ${statistics.median(costs):.4f}")
            print(f"  total cost        ${sum(costs):.2f}")

    tools = Counter(t for r in results for t in r.get("investigation", {}).get("tools_used", []))
    if tools:
        print(f"\n{B}Tools the agent actually reached for{O}")
        for name, count in tools.most_common(8):
            print(f"  {name:<28} {count}")

    print(f"\n{D}full per-run data: {out_path}{O}")
    print(f"{D}verify the ledger these runs produced: python scripts/verify_chain.py{O}\n")


if __name__ == "__main__":
    raise SystemExit(main())
