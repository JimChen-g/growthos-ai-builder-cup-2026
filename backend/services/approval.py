"""Concurrency-safe in-memory approval workflow for the Phase 2 runtime."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from backend.models.actions import ProposedAction
from backend.models.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRecord,
    ApprovalStatus,
)


class ApprovalError(RuntimeError):
    """Base error for invalid approval operations."""


class ApprovalNotFoundError(ApprovalError):
    pass


class InvalidApprovalTransitionError(ApprovalError):
    pass


class StaleApprovalError(ApprovalError):
    pass


class ApprovalService:
    """Own the state machine and atomically claim approved actions for execution."""

    def __init__(self, *, approval_id_factory: Callable[[], str] | None = None) -> None:
        self._approval_id_factory = approval_id_factory or (lambda: str(uuid4()))
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def request(self, *, workflow_run_id: str, action: ProposedAction) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=self._approval_id_factory(),
            workflow_run_id=workflow_run_id,
            action=action,
            action_digest=action.digest(),
            status=ApprovalStatus.PENDING,
        )
        async with self._lock:
            self._records[record.approval_id] = record
        return record.model_copy(deep=True)

    async def get(self, approval_id: str) -> ApprovalRecord:
        async with self._lock:
            return self._copy_record(approval_id)

    async def list(self, *, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        async with self._lock:
            records = list(self._records.values())
            if status is not None:
                records = [record for record in records if record.status is status]
            return [record.model_copy(deep=True) for record in records]

    async def decide(self, approval_id: str, decision: ApprovalDecision) -> ApprovalRecord:
        async with self._lock:
            current = self._record(approval_id)
            if current.status is not ApprovalStatus.PENDING:
                raise InvalidApprovalTransitionError(
                    f"approval {approval_id} is {current.status}, not pending"
                )
            if decision.action_digest != current.action_digest:
                raise StaleApprovalError("the action changed after it was presented for approval")
            next_status = (
                ApprovalStatus.APPROVED
                if decision.outcome is ApprovalOutcome.APPROVE
                else ApprovalStatus.REJECTED
            )
            updated = current.model_copy(
                update={
                    "status": next_status,
                    "decided_at": datetime.now(UTC),
                    "decided_by": decision.actor,
                    "decision_reason": decision.reason,
                }
            )
            self._records[approval_id] = updated
            return updated.model_copy(deep=True)

    async def claim_for_execution(self, approval_id: str) -> ApprovalRecord:
        """Atomically change approved → executing to prevent duplicate writes."""

        async with self._lock:
            current = self._record(approval_id)
            if current.status is not ApprovalStatus.APPROVED:
                raise InvalidApprovalTransitionError(
                    f"approval {approval_id} is {current.status}, not approved"
                )
            if current.action.digest() != current.action_digest:
                raise StaleApprovalError("approved action digest no longer matches its payload")
            updated = current.model_copy(update={"status": ApprovalStatus.EXECUTING})
            self._records[approval_id] = updated
            return updated.model_copy(deep=True)

    async def mark_executed(self, approval_id: str, *, result: dict[str, Any]) -> ApprovalRecord:
        return await self._finish_execution(
            approval_id,
            status=ApprovalStatus.EXECUTED,
            execution_result=result,
            failure_message=None,
        )

    async def mark_failed(self, approval_id: str, *, message: str) -> ApprovalRecord:
        return await self._finish_execution(
            approval_id,
            status=ApprovalStatus.FAILED,
            execution_result=None,
            failure_message=message[:1000],
        )

    async def _finish_execution(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        execution_result: dict[str, Any] | None,
        failure_message: str | None,
    ) -> ApprovalRecord:
        async with self._lock:
            current = self._record(approval_id)
            if current.status is not ApprovalStatus.EXECUTING:
                raise InvalidApprovalTransitionError(
                    f"approval {approval_id} is {current.status}, not executing"
                )
            updated = current.model_copy(
                update={
                    "status": status,
                    "execution_result": execution_result,
                    "failure_message": failure_message,
                }
            )
            self._records[approval_id] = updated
            return updated.model_copy(deep=True)

    def _record(self, approval_id: str) -> ApprovalRecord:
        try:
            return self._records[approval_id]
        except KeyError as error:
            raise ApprovalNotFoundError(f"approval {approval_id} was not found") from error

    def _copy_record(self, approval_id: str) -> ApprovalRecord:
        return self._record(approval_id).model_copy(deep=True)
