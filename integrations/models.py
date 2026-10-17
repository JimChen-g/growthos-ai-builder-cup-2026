"""Provider-neutral revenue and advertising snapshots."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DateRange(BaseModel):
    """Inclusive calendar date range used for comparable provider queries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "DateRange":
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        return self

    def previous_period(self) -> "DateRange":
        days = (self.end - self.start).days + 1
        previous_end = self.start - timedelta(days=1)
        return DateRange(start=previous_end - timedelta(days=days - 1), end=previous_end)


class RevenueSnapshot(BaseModel):
    """Normalized read-only revenue intelligence from Stripe."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["stripe"] = "stripe"
    period: DateRange
    currency: str = Field(min_length=3, max_length=3)
    revenue: Decimal = Field(ge=0)
    customers: int = Field(ge=0)
    mrr: Decimal = Field(ge=0)
    growth_rate_pct: Decimal | None = None
    momentum_score: Decimal = Field(ge=0, le=100)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CampaignPerformance(BaseModel):
    """Normalized campaign metrics shared by Google Ads and Meta Ads."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str
    campaign_name: str
    status: str | None = None
    spend: Decimal = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: Decimal = Field(ge=0)
    conversion_value: Decimal = Field(ge=0)
    roas: Decimal | None = Field(default=None, ge=0)
    cac: Decimal | None = Field(default=None, ge=0)


class CreativePerformance(BaseModel):
    """Meta ad-level creative rollup retained for later analysis."""

    model_config = ConfigDict(extra="forbid")

    creative_id: str
    creative_name: str
    campaign_id: str
    spend: Decimal = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: Decimal = Field(ge=0)


class AdsSnapshot(BaseModel):
    """Provider-neutral ad health summary."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["google_ads", "meta_ads"]
    period: DateRange
    currency: str = Field(default="USD", min_length=3, max_length=3)
    campaigns: list[CampaignPerformance]
    creatives: list[CreativePerformance] = Field(default_factory=list)
    spend: Decimal = Field(ge=0)
    clicks: int = Field(ge=0)
    conversions: Decimal = Field(ge=0)
    conversion_value: Decimal = Field(ge=0)
    roas: Decimal | None = Field(default=None, ge=0)
    cac: Decimal | None = Field(default=None, ge=0)
    trend_pct: Decimal | None = None
    cac_trend_pct: Decimal | None = None
    efficiency_score: Decimal = Field(ge=0, le=100)
    problems: list[str] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
