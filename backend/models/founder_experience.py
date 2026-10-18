"""Founder Daily Briefing, dashboard, and Phase 2 handoff contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from backend.models.connectors import DataProvenance
from backend.models.feedback import FeedbackAnalysis


class FounderMetricSnapshot(BaseModel):
    """Stable metric contract for the future Founder Daily Briefing and dashboard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    growth_score: Decimal = Field(ge=0, le=100)
    revenue: Decimal = Field(ge=0)
    marketing_roi: Decimal | None = Field(default=None, ge=0)
    ai_visibility: Decimal = Field(ge=0, le=100)
    customer_sentiment: Decimal | None = Field(default=None, ge=0, le=100)
    currency: str = Field(min_length=3, max_length=3)


class Phase3Handoff(BaseModel):
    """Read-only output from Phase 2 consumed by the Phase 3 experience."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metrics: FounderMetricSnapshot
    top_recommendation: str
    pending_approval_id: str | None = None


class MetricTone(StrEnum):
    POSITIVE = "positive"
    WATCH = "watch"
    ATTENTION = "attention"
    NEUTRAL = "neutral"


class BriefingSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    current_value: str
    change_pct: Decimal | None
    context: str
    provenance: DataProvenance


class FounderDailyBriefing(BaseModel):
    """The first-screen founder briefing, grounded in current system signals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    salutation: str
    company_name: str
    growth_health: Decimal = Field(ge=0, le=100)
    health_band: str
    executive_summary: str
    yesterday: list[BriefingSignal] = Field(min_length=3, max_length=3)
    main_risk: str
    risk_detail: str
    top_recommendation: str
    recommendation_reason: str
    provenance: DataProvenance
    pending_approval_id: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DashboardMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    value: Decimal | None = Field(ge=0)
    formatted_value: str
    trend: str
    source: str
    provenance: DataProvenance
    tone: MetricTone


class DashboardRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority: int = Field(ge=1)
    source_agent: str
    action: str
    rationale: str
    provenance: DataProvenance
    workflow_run_id: str | None = None
    approval_id: str | None = None
    approval_status: str | None = None


class GrowthDashboard(BaseModel):
    """Five-metric Phase 3 dashboard plus Phase 2 recommendations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company_name: str
    metrics: list[DashboardMetric] = Field(min_length=5, max_length=5)
    recommendations: list[DashboardRecommendation] = Field(min_length=1, max_length=5)
    customer_intelligence: FeedbackAnalysis
    customer_intelligence_provenance: DataProvenance
    provenance: DataProvenance
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FounderExperience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    briefing: FounderDailyBriefing
    dashboard: GrowthDashboard
