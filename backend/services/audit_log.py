"""Concurrency-safe append-only Agent Action Ledger."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from backend.models.audit_log import (
    AgentActionLedger,
    AgentActionLedgerEntry,
    AgentActionStatus,
)
from backend.models.connectors import DataMode


class AuditLogService:
    """Record immutable agent events and expose a bounded newest-first view."""

    def __init__(
        self,
        mode: DataMode = DataMode.DEMO,
        *,
        entry_id_factory: Callable[[], str] | None = None,
        timestamp_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.mode = mode
        self._entry_id_factory = entry_id_factory or (lambda: str(uuid4()))
        self._timestamp_factory = timestamp_factory or (lambda: datetime.now(UTC))
        self._entries: list[AgentActionLedgerEntry] = []
        self._idempotent_entries: dict[str, AgentActionLedgerEntry] = {}
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        agent: str,
        action: str,
        reason: str,
        status: AgentActionStatus,
        workflow_run_id: str | None = None,
        approval_id: str | None = None,
        timestamp: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> AgentActionLedgerEntry:
        """Append one event, or return the prior event for a repeated workflow key."""

        async with self._lock:
            if idempotency_key is not None and idempotency_key in self._idempotent_entries:
                return self._idempotent_entries[idempotency_key].model_copy(deep=True)

            values = {
                "entry_id": self._entry_id_factory(),
                "mode": self.mode,
                "agent": agent,
                "action": action,
                "reason": reason,
                "status": status,
                "workflow_run_id": workflow_run_id,
                "approval_id": approval_id,
                "timestamp": timestamp or self._timestamp_factory(),
            }
            entry = AgentActionLedgerEntry.model_validate(values)
            self._entries.append(entry)
            if idempotency_key is not None:
                self._idempotent_entries[idempotency_key] = entry
            return entry.model_copy(deep=True)

    async def all_entries(self) -> list[AgentActionLedgerEntry]:
        """Return every recorded entry in insertion order (used for integrity proofs)."""

        async with self._lock:
            return [entry.model_copy(deep=True) for entry in self._entries]

    async def list(
        self,
        *,
        status: AgentActionStatus | None = None,
        limit: int = 50,
    ) -> AgentActionLedger:
        """Return a snapshot without exposing mutable internal state."""

        async with self._lock:
            selected = self._entries
            if status is not None:
                selected = [entry for entry in selected if entry.status is status]
            ordered = sorted(selected, key=lambda entry: entry.timestamp, reverse=True)
            return AgentActionLedger(
                mode=self.mode,
                entries=[entry.model_copy(deep=True) for entry in ordered[:limit]],
                total=len(ordered),
            )
