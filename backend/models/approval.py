"""Approval records and their allowed state transitions."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models.actions import ProposedAction


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalOutcome(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalDecision(BaseModel):
    """Human decision captured with actor and optional rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    outcome: ApprovalOutcome
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)
    action_digest: str = Field(min_length=64, max_length=64)


class ApprovalRecord(BaseModel):
    """Immutable snapshot returned from the approval store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str
    workflow_run_id: str
    action: ProposedAction
    action_digest: str
    status: ApprovalStatus
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    execution_result: dict[str, Any] | None = None
    failure_message: str | None = None
