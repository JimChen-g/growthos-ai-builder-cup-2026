"""File-backed intelligence snapshots with before/after comparisons."""

import asyncio
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backend.models.audit_log import AgentActionLedgerEntry, AgentActionStatus
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
        async with self._lock:
            snapshots = [
                item
                for item in self._read()
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
