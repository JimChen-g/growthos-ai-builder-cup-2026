"""Immutable Agent Action Ledger contracts for the Phase 4 trust layer."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from backend.models.connectors import DataMode


class AgentActionStatus(StrEnum):
    """Founder-facing outcome of one recorded agent action."""

    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class AgentActionLedgerEntry(BaseModel):
    """One append-only audit event emitted by an agent workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    entry_id: str = Field(min_length=1)
    mode: DataMode
    agent: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=500)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: AgentActionStatus
    workflow_run_id: str | None = None
    approval_id: str | None = None


class AgentActionLedger(BaseModel):
    """Newest-first audit-log response with the filtered total."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DataMode
    entries: list[AgentActionLedgerEntry]
    total: int = Field(ge=0)
