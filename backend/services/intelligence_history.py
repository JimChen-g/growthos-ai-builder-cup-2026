"""Local and workspace-scoped persistent intelligence history and Live audit events."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.api_core.exceptions import AlreadyExists

from backend.models.audit_log import AgentActionLedger, AgentActionLedgerEntry, AgentActionStatus
from backend.models.connectors import (
    DataMode,
    FeedbackCollectionResponse,
    GeoScanReport,
)
from backend.models.intelligence_history import (
    IntelligenceHistory,
    IntelligenceHistoryItem,
    IntelligenceKind,
    IntelligenceSnapshot,
    ObservedActionEffect,
)

logger = logging.getLogger(__name__)

_MEASUREMENT_ACTIONS = {
    "Assessed live AI search visibility",
    "Collected live customer feedback",
}
_MEASUREMENT_AGENTS = {"Growth Intelligence Engine"}


def _is_measurement(entry: AgentActionLedgerEntry) -> bool:
    """Measurements observe the business; they are never an optimization action."""

    return (
        entry.action in _MEASUREMENT_ACTIONS
        or entry.agent in _MEASUREMENT_AGENTS
        or entry.action.startswith("Measured ")
    )


class IntelligenceHistoryService:
    """Save complete results and derive observational deltas between runs."""

    def __init__(self, mode: DataMode, path: Path) -> None:
        self.mode = mode
        self.path = path
        self._lock = asyncio.Lock()

    async def record_geo(
        self,
        report: GeoScanReport,
        *,
        related_action_ids: list[str] | None = None,
    ) -> IntelligenceSnapshot:
        metrics = {
            "visibility_score": report.overall_visibility_score,
            "brand_mentions": Decimal(sum(item.brand_mentions for item in report.providers)),
            "owned_citations": Decimal(sum(item.branded_citations for item in report.providers)),
            "successful_prompts": Decimal(
                sum(item.successful_prompts for item in report.providers)
            ),
        }
        snapshot = IntelligenceSnapshot(
            snapshot_id=str(uuid4()),
            tenant_id=report.tenant_id,
            mode=report.mode,
            kind=IntelligenceKind.BRAND_VISIBILITY,
            brand=report.brand,
            score=report.overall_visibility_score,
            metrics=metrics,
            summary=(
                f"{int(metrics['brand_mentions'])} brand mentions and "
                f"{int(metrics['owned_citations'])} owned citations"
            ),
            result=report.model_dump(mode="json"),
            related_action_ids=related_action_ids or [],
            generated_at=report.generated_at,
        )
        return await self._append(snapshot)

    async def record_feedback(
        self,
        response: FeedbackCollectionResponse,
        *,
        related_action_ids: list[str] | None = None,
    ) -> IntelligenceSnapshot:
        analysis = response.analysis
        metrics = {
            "signal_count": Decimal(len(response.signals)),
            "sentiment_score": Decimal(str(analysis.sentiment_score)) if analysis else Decimal(0),
            "positive_pct": Decimal(str(analysis.positive_pct)) if analysis else Decimal(0),
            "negative_pct": Decimal(str(analysis.negative_pct)) if analysis else Decimal(0),
        }
        snapshot = IntelligenceSnapshot(
            snapshot_id=str(uuid4()),
            tenant_id=response.tenant_id,
            mode=response.mode,
            kind=IntelligenceKind.CUSTOMER_INTELLIGENCE,
            brand=response.brand,
            score=metrics["sentiment_score"] if analysis else None,
            metrics=metrics,
            summary=analysis.main_problem if analysis else "No usable customer comments found",
            result=response.model_dump(mode="json"),
            related_action_ids=related_action_ids or [],
            generated_at=response.generated_at,
        )
        return await self._append(snapshot)

    async def list_snapshots(
        self,
        *,
        tenant_id: str,
        kind: IntelligenceKind | None = None,
        limit: int = 50,
        audit_entries: Iterable[AgentActionLedgerEntry] = (),
    ) -> IntelligenceHistory:
        snapshots = [
            item
            for item in await self._load_snapshots()
            if item.tenant_id == tenant_id and (kind is None or item.kind is kind)
        ]

        snapshots.sort(key=lambda item: item.generated_at)
        entries = list(audit_entries)
        history_items: list[IntelligenceHistoryItem] = []
        effects: list[ObservedActionEffect] = []
        # Compare a result only with the previous check of the same kind *and* brand.
        previous_by_subject: dict[tuple[IntelligenceKind, str], IntelligenceSnapshot] = {}

        for snapshot in snapshots:
            subject = (snapshot.kind, snapshot.brand.casefold())
            previous = previous_by_subject.get(subject)
            score_delta = self._delta(snapshot.score, previous.score if previous else None)
            metric_deltas = {
                key: value - previous.metrics[key]
                for key, value in snapshot.metrics.items()
                if previous and key in previous.metrics
            }
            observed = self._effects_between(previous, snapshot, score_delta, entries)
            effects.extend(observed)
            history_items.append(
                IntelligenceHistoryItem(
                    snapshot=snapshot,
                    previous_snapshot_id=previous.snapshot_id if previous else None,
                    score_delta=score_delta,
                    metric_deltas=metric_deltas,
                    trend=self._trend(score_delta),
                    observed_after_actions=observed,
                )
            )
            previous_by_subject[subject] = snapshot

        newest = list(reversed(history_items))[:limit]
        top_effects = sorted(
            effects,
            key=lambda item: (item.observed_score_delta, item.timestamp),
            reverse=True,
        )[:5]
        return IntelligenceHistory(
            mode=self.mode,
            kind=kind,
            items=newest,
            total=len(history_items),
            top_observed_effects=top_effects,
        )

    async def _load_snapshots(self) -> list[IntelligenceSnapshot]:
        async with self._lock:
            return self._read()

    async def _append(self, snapshot: IntelligenceSnapshot) -> IntelligenceSnapshot:
        if snapshot.mode is not self.mode:
            raise ValueError("snapshot mode does not match the selected history store")
        async with self._lock:
            snapshots = self._read()
            snapshots.append(snapshot)
            self._write(snapshots)
        return snapshot.model_copy(deep=True)

    def _read(self) -> list[IntelligenceSnapshot]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("history file must contain a JSON list")
            return [IntelligenceSnapshot.model_validate(item) for item in payload]
        except (OSError, ValueError, TypeError) as error:
            # Never overwrite unreadable history: keep the original next to a fresh store.
            backup = self.path.with_name(
                f"{self.path.name}.unreadable-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
            )
            try:
                self.path.replace(backup)
            except OSError:
                logger.exception("Could not preserve unreadable history file %s", self.path)
                raise
            logger.error(
                "Intelligence history %s could not be read (%s); preserved as %s",
                self.path,
                error,
                backup,
            )
            return []

    def _write(self, snapshots: list[IntelligenceSnapshot]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in snapshots],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def _delta(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
        if current is None or previous is None:
            return None
        return current - previous

    @staticmethod
    def _trend(delta: Decimal | None) -> str:
        if delta is None:
            return "baseline"
        if delta > 0:
            return "improved"
        if delta < 0:
            return "declined"
        return "unchanged"

    @staticmethod
    def _effects_between(
        previous: IntelligenceSnapshot | None,
        current: IntelligenceSnapshot,
        score_delta: Decimal | None,
        audit_entries: list[AgentActionLedgerEntry],
    ) -> list[ObservedActionEffect]:
        if previous is None or score_delta is None:
            return []
        return [
            ObservedActionEffect(
                entry_id=entry.entry_id,
                agent=entry.agent,
                action=entry.action,
                timestamp=entry.timestamp,
                observed_score_delta=score_delta,
                measurement_kind=current.kind,
                current_snapshot_id=current.snapshot_id,
            )
            for entry in audit_entries
            if entry.status is AgentActionStatus.EXECUTED
            and not _is_measurement(entry)
            and previous.generated_at < entry.timestamp <= current.generated_at
        ]


class _FirestoreWorkspaceStore:
    """Bind all document access to a workspace already verified by the API layer."""

    def __init__(
        self,
        project_id: str,
        database_id: str,
        workspace_id: str,
        retention_days: int = 7,
        *,
        client: Any = None,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", workspace_id):
            raise ValueError("invalid server-derived Live workspace id")
        if not 1 <= retention_days <= 365:
            raise ValueError("Live retention must be between 1 and 365 days")
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=project_id, database=database_id)
        self.workspace_id = workspace_id
        self._client = client
        self._retention_days = retention_days
        self._workspace = client.collection("live_workspaces").document(workspace_id)

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError("Live data does not belong to the authenticated workspace")

    def _expires_at(self) -> datetime:
        return datetime.now(UTC) + timedelta(days=self._retention_days)

    @staticmethod
    def _retained(payload: dict[str, Any]) -> bool:
        expires_at = payload.get("expires_at")
        # Firestore TTL is asynchronous. Expired or malformed records are never served
        # while waiting for physical deletion, and never become immortal by accident.
        return (
            isinstance(expires_at, datetime)
            and expires_at.tzinfo is not None
            and expires_at > datetime.now(UTC)
        )


class FirestoreIntelligenceHistoryService(_FirestoreWorkspaceStore, IntelligenceHistoryService):
    """Durable Live snapshots; no file writes or cross-workspace result cache."""

    mode = DataMode.LIVE

    async def list_snapshots(
        self,
        *,
        tenant_id: str,
        kind: IntelligenceKind | None = None,
        limit: int = 50,
        audit_entries: Iterable[AgentActionLedgerEntry] = (),
    ) -> IntelligenceHistory:
        self._assert_workspace(tenant_id)
        return await super().list_snapshots(
            tenant_id=tenant_id, kind=kind, limit=limit, audit_entries=audit_entries
        )

    async def _append(self, snapshot: IntelligenceSnapshot) -> IntelligenceSnapshot:
        self._assert_workspace(snapshot.tenant_id)
        if snapshot.mode is not DataMode.LIVE:
            raise ValueError("only Live snapshots belong in the hosted Live history")
        await asyncio.to_thread(
            self._workspace.collection("intelligence_history").document(snapshot.snapshot_id).create,
            {"snapshot": snapshot.model_dump(mode="json"), "expires_at": self._expires_at()},
        )
        return snapshot.model_copy(deep=True)

    async def _load_snapshots(self) -> list[IntelligenceSnapshot]:
        def read() -> list[IntelligenceSnapshot]:
            snapshots = []
            for document in self._workspace.collection("intelligence_history").stream():
                payload = document.to_dict()
                if not self._retained(payload):
                    continue
                snapshot = IntelligenceSnapshot.model_validate(payload["snapshot"])
                self._assert_workspace(snapshot.tenant_id)
                if snapshot.mode is not DataMode.LIVE:
                    raise ValueError("stored Live history has an invalid mode")
                snapshots.append(snapshot)
            return snapshots

        return await asyncio.to_thread(read)


class FirestoreAuditLogService(_FirestoreWorkspaceStore):
    """Append-only Live audit records with cross-instance idempotent creation."""

    mode = DataMode.LIVE

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
        entry_id = (
            hashlib.sha256(idempotency_key.encode()).hexdigest()
            if idempotency_key is not None
            else str(uuid4())
        )
        entry = AgentActionLedgerEntry(
            entry_id=entry_id,
            mode=self.mode,
            agent=agent,
            action=action,
            reason=reason,
            status=status,
            workflow_run_id=workflow_run_id,
            approval_id=approval_id,
            timestamp=timestamp or datetime.now(UTC),
        )
        reference = self._workspace.collection("audit_log").document(entry_id)

        def create() -> AgentActionLedgerEntry:
            try:
                # create() has an exists=false precondition. Concurrent deliveries on
                # separate Cloud Run instances cannot replace or duplicate this event.
                reference.create(
                    {"entry": entry.model_dump(mode="json"), "expires_at": self._expires_at()}
                )
            except AlreadyExists:
                if idempotency_key is None:
                    raise
                existing = reference.get()
                if not existing.exists:
                    raise RuntimeError("the retained audit record could not be read") from None
                return self._entry(existing.to_dict())
            return entry.model_copy(deep=True)

        return await asyncio.to_thread(create)

    @staticmethod
    def _entry(payload: dict[str, Any]) -> AgentActionLedgerEntry:
        entry = AgentActionLedgerEntry.model_validate(payload["entry"])
        if entry.mode is not DataMode.LIVE:
            raise ValueError("stored Live audit record has an invalid mode")
        return entry

    async def all_entries(self) -> list[AgentActionLedgerEntry]:
        def read() -> list[AgentActionLedgerEntry]:
            entries = []
            for document in self._workspace.collection("audit_log").stream():
                payload = document.to_dict()
                if self._retained(payload):
                    entries.append(self._entry(payload))
            return sorted(entries, key=lambda entry: (entry.timestamp, entry.entry_id))

        return await asyncio.to_thread(read)

    async def list(
        self,
        *,
        status: AgentActionStatus | None = None,
        limit: int = 50,
    ) -> AgentActionLedger:
        entries = await self.all_entries()
        selected = [entry for entry in entries if status is None or entry.status is status]
        return AgentActionLedger(
            mode=self.mode, entries=list(reversed(selected))[:limit], total=len(selected)
        )
