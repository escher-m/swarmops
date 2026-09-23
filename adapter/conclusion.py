"""The structured shape we require HolmesGPT's conclusion to take.

Asking the model for prose and then parsing it is the single most fragile part
of any agent integration, and in an audit ledger a mis-parse is not a cosmetic
bug -- it silently writes a false record. So the conclusion is requested as a
schema-constrained response instead: the model fills these fields or the call
fails loudly.

The fields line up deliberately with two things at once: the `proposed_action`
block in agent_action_record.schema.json, and the remediation actions the
seeded service actually exposes. That alignment is what lets an approved
proposal be executed verbatim rather than re-interpreted at execution time --
the approver approves the exact command that later runs.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FailureMode = Literal["bad_deploy", "pool_exhaustion", "slow_query", "leaked_secret", "unknown"]

RemediationAction = Literal[
    "rollback_deploy",
    "increase_pool_size",
    "restore_revenue_index",
    "rotate_credential",
    "none",
]

ChangeType = Literal[
    "config_change", "rollback_deploy", "scale_resource", "restart_workload",
    "code_change", "secret_rotation", "schema_migration", "no_action",
]

BlastRadius = Literal["single_resource", "single_service", "multi_service", "environment_wide"]


class ProposedFix(BaseModel):
    description: str = Field(
        description="Plain-language statement of the change and why it addresses the root cause. "
                    "Written for a human approver who has not read the logs."
    )
    change_type: ChangeType = Field(description="Category of the change.")
    command_or_diff: str = Field(
        description="The exact command or diff that would be applied, verbatim."
    )
    reversible: bool = Field(
        description="True only if the change can be fully undone without data loss. "
                    "If unsure, answer false."
    )
    blast_radius: BlastRadius = Field(description="How much is affected if this goes wrong.")
    remediation_action: RemediationAction = Field(
        description="Which supported remediation to invoke. Use 'none' if no safe fix is available."
    )
    remediation_params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameters for the remediation, e.g. {'target_version': 'v1.4.1'} "
                    "or {'db_pool_max': '10'}. Empty when not needed.",
    )


class InvestigationConclusion(BaseModel):
    root_cause: str = Field(
        description="What is actually broken and why, in two or three sentences, "
                    "citing the specific evidence that establishes it."
    )
    failure_mode: FailureMode = Field(
        description="Which known failure class this is, or 'unknown' if none fits."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How certain the root cause is, given the evidence gathered."
    )
    affected_resource: str = Field(
        description="The resource identifier most affected, e.g. 'staging/checkout-api'."
    )
    user_impact: str = Field(
        description="What a user of the service experiences as a result."
    )
    proposed_fix: Optional[ProposedFix] = Field(
        default=None,
        description="The remediation to propose, or null if no safe remediation was identified.",
    )
