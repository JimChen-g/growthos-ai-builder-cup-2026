"""Provider-neutral revenue and advertising snapshots."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from integrations.base import IntegrationError


class AccountTimezoneError(IntegrationError):
    """Safe failure when a source cannot establish its reporting day boundaries."""

    def __init__(self) -> None:
        super().__init__(
            "The provider did not return a supported account reporting timezone. "
            "The report was not generated; check the account settings and try again."
        )


def verified_reporting_timezone(value: object) -> str:
    """Accept actual IANA account metadata, never guess UTC from missing data."""
    if not isinstance(value, str) or not value.strip():
        raise AccountTimezoneError()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise AccountTimezoneError() from None
    return value


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
    account_environment: Literal["test", "live", "unknown"] = "unknown"
    period: DateRange
    reporting_timezone: str = "UTC"
    currency: str = Field(min_length=3, max_length=3)
    revenue: Decimal = Field(ge=0)
    customers: int = Field(ge=0)
    mrr: Decimal | None = Field(ge=0)
    mrr_status: Literal["complete", "incomplete"] = "complete"
    mrr_policy: str = "net_recurring_v1"
    mrr_warnings: list[str] = Field(default_factory=list)
    mrr_as_of: datetime | None = None
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
    account_environment: Literal["test", "live", "unknown"] = "unknown"
    period: DateRange
    # Historical demo fixtures did not record a timezone; Live adapters must verify one.
    reporting_timezone: str | None = None
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
