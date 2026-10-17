"""Persisted GEO and customer-intelligence measurement history."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models.connectors import DataMode


class IntelligenceKind(StrEnum):
    BRAND_VISIBILITY = "brand_visibility"
    CUSTOMER_INTELLIGENCE = "customer_intelligence"


class IntelligenceSnapshot(BaseModel):
    """One complete, reusable measurement saved without another provider call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    mode: DataMode
    kind: IntelligenceKind
    brand: str = Field(min_length=1)
    score: Decimal | None = Field(default=None, ge=0, le=100)
    metrics: dict[str, Decimal] = Field(default_factory=dict)
    summary: str
    result: dict[str, Any]
    related_action_ids: list[str] = Field(default_factory=list)
    generated_at: datetime
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ObservedActionEffect(BaseModel):
    """An outcome observed after an action; deliberately not a causal claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str
    agent: str
    action: str
    timestamp: datetime
    observed_score_delta: Decimal
    measurement_kind: IntelligenceKind
    current_snapshot_id: str


class IntelligenceHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: IntelligenceSnapshot
    previous_snapshot_id: str | None = None
    score_delta: Decimal | None = None
    metric_deltas: dict[str, Decimal] = Field(default_factory=dict)
    trend: str
    observed_after_actions: list[ObservedActionEffect] = Field(default_factory=list)


class IntelligenceHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DataMode
    kind: IntelligenceKind | None = None
    items: list[IntelligenceHistoryItem]
    total: int = Field(ge=0)
    top_observed_effects: list[ObservedActionEffect] = Field(default_factory=list)
