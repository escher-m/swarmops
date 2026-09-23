"""Wrap HolmesGPT and record everything it does into the ledger.

This is the piece that makes the product vendor-neutral in practice rather than
in principle. HolmesGPT knows nothing about the ledger; it is handed a toolset
and asked a question. The adapter sits underneath, on the one seam every agent
framework has -- tool invocation -- and turns each call into evidence.

Interception happens by subclassing HolmesGPT's own YAMLTool and overriding
`invoke`, rather than by monkeypatching. That matters for a compliance product:
a subclass is visible in a stack trace and survives a library upgrade loudly
(the override stops matching and tests fail) instead of silently (a patched
attribute quietly stops being consulted).

What lands in the ledger from one investigation:

  * one `investigate` record per tool call, carrying the exact command HolmesGPT
    ran and the exact output it saw;
  * one final `investigate` record carrying the structured conclusion, linked to
    the last step;
  * one `propose_fix` record when a remediation was identified, and nothing that
    can execute it -- the adapter has no write tools at all.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from holmes.core.llm import DefaultLLM
from holmes.core.tool_calling_llm import ToolCallingLLM
from holmes.core.tools import StructuredToolResult, ToolInvokeContext, YAMLTool, YAMLToolset
from holmes.core.tools_utils.tool_executor import ToolExecutor

from adapter.conclusion import InvestigationConclusion
from ledger.recorder import Actor, PolicyRefusal, Recorder, Target
from ledger.store import AppendResult, utcnow

ROOT = Path(__file__).resolve().parent.parent
TOOLSET_PATH = ROOT / "adapter" / "toolsets" / "seedapp.yaml"

HOLMES_VERSION = "0.42.0"

# Which evidence_type a given tool's output should be recorded as. Mapping this
# explicitly, rather than labelling everything "command_output", is what lets an
# auditor filter the ledger by the kind of evidence an action rested on.
EVIDENCE_TYPE_BY_TOOL = {
    "checkout_service_logs": "log_query",
    "checkout_service_health": "http_probe",
    "probe_checkout_endpoint": "http_probe",
    "probe_orders_endpoint": "http_probe",
    "probe_revenue_report": "http_probe",
    "checkout_service_metrics": "metric_query",
    "checkout_deployment_state": "config_snapshot",
    "git_recent_commits": "git_history",
    "git_show_commit": "diff",
    "git_scan_for_secrets": "git_history",
    "database_query_plan": "trace",
    "database_indexes": "config_snapshot",
}

# Patterns whose matches are replaced before evidence is stored. A ledger that
# faithfully records a leaked credential has helpfully made a second copy of it.
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AKIA****************"),
    (r"(aws_secret_access_key\s*=\s*['\"])[^'\"]{20,}(['\"])", r"\1********************\2"),
    (r"(AWS_SECRET_ACCESS_KEY\s*=\s*['\"])[^'\"]{20,}(['\"])", r"\1********************\2"),
]


@dataclass
class ToolInvocation:
    """One tool call HolmesGPT made, as observed at the seam."""

    tool_name: str
    params: dict[str, Any]
    invocation: str
    output: str
    status: str
    elapsed_ms: int
    at: str


# Set for the duration of one investigation. A ContextVar rather than a module
# global so that concurrent investigations in different threads cannot record
# into each other's transcripts.
_SINK: ContextVar[list[ToolInvocation] | None] = ContextVar("_SINK", default=None)


def _redact(text: str) -> tuple[str, bool]:
    import re

    redacted = False
    for pattern, replacement in SECRET_PATTERNS:
        text, n = re.subn(pattern, replacement, text)
        redacted = redacted or bool(n)
    return text, redacted


class RecordingTool(YAMLTool):
    """A HolmesGPT tool that reports every invocation to the active sink."""

    def invoke(self, params: dict, context: ToolInvokeContext) -> StructuredToolResult:
        started = time.perf_counter()
        at = utcnow()
        try:
            result = super().invoke(params, context)
        except Exception as exc:
            # A tool that raised still tells us what the agent attempted, and an
            # attempted-but-failed diagnostic is itself audit-relevant.
            sink = _SINK.get()
            if sink is not None:
                sink.append(ToolInvocation(
                    tool_name=self.name, params=dict(params),
                    invocation=f"{self.name}({params})",
                    output=f"{type(exc).__name__}: {exc}",
                    status="ERROR",
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    at=at,
                ))
            raise

        sink = _SINK.get()
        if sink is not None:
            data = result.data if isinstance(result.data, str) else json.dumps(result.data, default=str)
            sink.append(ToolInvocation(
                tool_name=self.name,
                params=dict(params),
                invocation=result.invocation or f"{self.name}({params})",
                output=(data or "") + (f"\n[error] {result.error}" if result.error else ""),
                status=str(getattr(result.status, "name", result.status)),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                at=at,
            ))
        return result


def build_toolset(seedapp_base: str) -> YAMLToolset:
    """Load the seeded-service toolset with recording tools substituted in."""
    os.environ.setdefault("SWARMOPS_ROOT", str(ROOT))
    os.environ["SEEDAPP_BASE"] = seedapp_base

    doc = yaml.safe_load(TOOLSET_PATH.read_text())
    (name, body), = doc["toolsets"].items()
    toolset = YAMLToolset(name=name, **body)
    # Rebuild each tool as a RecordingTool. model_dump/revalidate keeps us honest
    # about the field set rather than reaching into private attributes.
    toolset.tools = [RecordingTool(**tool.model_dump()) for tool in toolset.tools]
    toolset.check_prerequisites()
    return toolset


@dataclass
class InvestigationResult:
    incident_id: str
    invocation_id: str
    conclusion: InvestigationConclusion | None
    tool_invocations: list[ToolInvocation]
    step_record_ids: list[str] = field(default_factory=list)
    conclusion_record_id: str | None = None
    propose_record_id: str | None = None
    proposed_action: dict[str, Any] | None = None
    error: str | None = None
    llm_cost_usd: float | None = None
    total_tokens: int | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.conclusion is not None


SYSTEM_PROMPT = """\
You are an SRE investigating a production incident on the `checkout-api` service.

Use the tools available to gather evidence before concluding. Work like an
engineer would: reproduce the symptom, read the logs, look at what changed, and
confirm your hypothesis against a second source before committing to it. Do not
guess a root cause you have not evidenced.

You have read-only access. You cannot change anything, and you must not try;
propose a remediation instead and a human will decide whether to apply it.

Supported remediations, and when each applies:
  rollback_deploy       a recent release introduced the fault; roll back to the
                        last good tag. params: {"target_version": "v1.4.1"}
  increase_pool_size    the database connection pool is too small for the load.
                        params: {"db_pool_max": "10"}
  restore_revenue_index a query is slow because its supporting index is missing
                        and the report issues one query per customer. params: {}
  rotate_credential     a credential is exposed and must be rotated.
                        params: {"new_credential_id": "AKIA2F7QXVEXAMPLE002"}

Set `reversible` to false whenever you are not certain the change can be fully
undone. Be concrete in `command_or_diff`: it is what a human will approve and
what will then be executed verbatim.
"""


class HolmesAdapter:
    """Runs a HolmesGPT investigation and records it to the ledger."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        seedapp_base: str = "http://127.0.0.1:8081",
        max_steps: int = 14,
    ):
        self.model = model or os.environ.get("HOLMES_MODEL", "anthropic/claude-sonnet-5")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.seedapp_base = seedapp_base
        self.max_steps = max_steps
        self._toolset = build_toolset(seedapp_base)

    def _tool_calling_llm(self) -> ToolCallingLLM:
        llm = DefaultLLM(model=self.model, api_key=self.api_key)
        return ToolCallingLLM(
            tool_executor=ToolExecutor([self._toolset]),
            max_steps=self.max_steps,
            llm=llm,
            tool_results_dir=None,
        )

    def investigate(
        self,
        *,
        incident_id: str,
        alert: str,
        environment: str = "staging",
        resource_id: str = "staging/checkout-api",
        resource_type: str = "k8s_deployment",
    ) -> InvestigationResult:
        """Run one investigation end to end, recording every step."""
        invocation_id = f"holmes-{uuid.uuid4().hex[:12]}"
        actor = Actor(
            agent_name="holmesgpt-investigator",
            agent_version=HOLMES_VERSION,
            tool_or_platform="holmesgpt",
            invocation_id=invocation_id,
            model=self.model,
        )
        recorder = Recorder(
            incident_id=incident_id,
            actor=actor,
            target=Target(resource_type, resource_id, environment),
        )
        result = InvestigationResult(
            incident_id=incident_id, invocation_id=invocation_id,
            conclusion=None, tool_invocations=[],
        )

        sink: list[ToolInvocation] = []
        token = _SINK.set(sink)
        started = time.perf_counter()
        try:
            llm_result = self._tool_calling_llm().call(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": alert},
                ],
                response_format=InvestigationConclusion,
            )
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.tool_invocations = list(sink)
            result.duration_ms = int((time.perf_counter() - started) * 1000)
            _SINK.reset(token)
            self._record_steps(recorder, result)
            return result
        finally:
            _SINK.reset(token)

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        result.tool_invocations = list(sink)
        result.llm_cost_usd = getattr(llm_result, "total_cost", None)
        result.total_tokens = getattr(llm_result, "total_tokens", None)

        raw = llm_result.result
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
            result.conclusion = InvestigationConclusion.model_validate(payload)
        except Exception as exc:
            result.error = (
                f"HolmesGPT returned a conclusion that does not match the required "
                f"schema ({type(exc).__name__}: {exc}). Raw: {str(raw)[:400]}"
            )

        self._record_steps(recorder, result)
        if result.conclusion is not None:
            self._record_conclusion(recorder, result)
            self._record_proposal(recorder, result)
        return result

    # ------------------------------------------------------------- recording

    def _evidence_for(self, recorder: Recorder, call: ToolInvocation) -> dict[str, Any]:
        output, redacted = _redact(call.output or "(no output)")
        return recorder.capture(
            evidence_type=EVIDENCE_TYPE_BY_TOOL.get(call.tool_name, "command_output"),
            source=call.invocation,
            content=output,
            retrieved_at=call.at,
            redacted=redacted,
        )

    def _record_steps(self, recorder: Recorder, result: InvestigationResult) -> None:
        """One investigate record per tool call HolmesGPT actually made."""
        for i, call in enumerate(result.tool_invocations, start=1):
            evidence = self._evidence_for(recorder, call)
            ok = call.status.upper() in {"SUCCESS", "NO_DATA"}
            appended = recorder.emit(
                action_type="investigate",
                summary=(
                    f"Step {i}/{len(result.tool_invocations)}: ran {call.tool_name} "
                    f"to gather {EVIDENCE_TYPE_BY_TOOL.get(call.tool_name, 'diagnostic')} evidence."
                ),
                outcome_status="success" if ok else "failure",
                outcome_details=(
                    f"Command: {call.invocation}\n"
                    f"Status: {call.status}. Returned {len(call.output or '')} bytes "
                    f"in {call.elapsed_ms}ms."
                ),
                evidence=[evidence],
                duration_ms=call.elapsed_ms,
            )
            result.step_record_ids.append(appended.record_id)

    def _record_conclusion(self, recorder: Recorder, result: InvestigationResult) -> None:
        c = result.conclusion
        assert c is not None
        reasoning = recorder.capture(
            evidence_type="agent_reasoning",
            source=f"holmesgpt conclusion (invocation {result.invocation_id}, model {self.model})",
            content=json.dumps(c.model_dump(), indent=2),
            content_type="application/json",
        )
        manifest = recorder.capture(
            evidence_type="agent_reasoning",
            source="evidence manifest: ledger records for each diagnostic step in this investigation",
            content="\n".join(
                f"{i}. {call.tool_name} -> record {rid}"
                for i, (call, rid) in enumerate(
                    zip(result.tool_invocations, result.step_record_ids), start=1)
            ) or "(no tool calls were made)",
        )
        appended = recorder.emit(
            action_type="investigate",
            summary=f"Concluded: {c.root_cause}"[:500],
            outcome_status="success",
            outcome_details=(
                f"Root cause ({c.confidence} confidence): {c.root_cause}\n"
                f"Failure mode: {c.failure_mode}\n"
                f"User impact: {c.user_impact}\n"
                f"Based on {len(result.tool_invocations)} diagnostic step(s)."
            ),
            evidence=[reasoning, manifest],
            in_response_to=result.step_record_ids[-1] if result.step_record_ids else None,
            duration_ms=result.duration_ms,
        )
        result.conclusion_record_id = appended.record_id

    def _record_proposal(self, recorder: Recorder, result: InvestigationResult) -> None:
        c = result.conclusion
        assert c is not None
        fix = c.proposed_fix
        if fix is None or fix.remediation_action == "none":
            return

        proposed_action = {
            "description": fix.description,
            "change_type": fix.change_type,
            "command_or_diff": fix.command_or_diff,
            "reversible": fix.reversible,
            "blast_radius": fix.blast_radius,
        }
        plan = recorder.capture(
            evidence_type="diff",
            source="proposed remediation, as returned by HolmesGPT",
            content=json.dumps(
                {**fix.model_dump(), "root_cause": c.root_cause}, indent=2
            ),
            content_type="application/json",
        )
        try:
            appended = recorder.emit(
                action_type="propose_fix",
                summary=f"Proposed: {fix.description}"[:500],
                outcome_status="pending",
                outcome_details=(
                    "Proposal recorded. No change has been applied; this proposal "
                    "must be approved by a human before anything executes."
                ),
                evidence=[plan],
                proposed_action=proposed_action,
                in_response_to=result.conclusion_record_id,
            )
        except PolicyRefusal as refusal:
            # Policy refused the proposal outright (for example an irreversible
            # change). The refusal is already in the ledger; surface it rather
            # than letting the run look like it simply found nothing.
            result.error = str(refusal)
            result.propose_record_id = refusal.record_id
            return

        result.propose_record_id = appended.record_id
        result.proposed_action = {**proposed_action,
                                  "remediation_action": fix.remediation_action,
                                  "remediation_params": fix.remediation_params}
