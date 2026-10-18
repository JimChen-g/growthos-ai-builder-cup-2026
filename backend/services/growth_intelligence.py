"""Phase 1 aggregation service with interchangeable demo and live data sources."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.config import Settings
from backend.models.company import CompanyProfile
from backend.services.growth_score import (
    GrowthScoreEngine,
    GrowthScoreInputs,
    GrowthScoreResult,
)
from integrations.base import IntegrationAPIError, IntegrationTransportError
from integrations.currency import AccountCurrencyMismatchError
from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.meta_ads import MetaAdsIntegration, MetaAdsIntegrationConfig
from integrations.metrics import (
    DEFAULT_TARGET_CONVERSION_RATE_PCT,
    DEFAULT_TARGET_ROAS,
    ads_efficiency_score,
    ratio,
    revenue_momentum_score,
)
from integrations.models import (
    AccountTimezoneError,
    AdsSnapshot,
    DateRange,
    RevenueSnapshot,
    verified_reporting_timezone,
)
from integrations.stripe import StripeIntegration, StripeIntegrationConfig

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class DataSourceMode(StrEnum):
    """Supported Phase 1 runtime data sources."""

    DEMO = "demo"
    LIVE = "live"


class RevenueSource(Protocol):
    """Structural contract implemented by Stripe and demo revenue sources."""

    async def collect(self, period: DateRange) -> RevenueSnapshot: ...


class AdsSource(Protocol):
    """Structural contract implemented by ad-platform and demo sources."""

    async def collect(self, period: DateRange) -> AdsSnapshot: ...


class GrowthIntelligenceRequest(BaseModel):
    """Company context and assessed inputs required for one analysis run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    company: CompanyProfile
    period: DateRange
    market_demand: Decimal = Field(ge=0, le=100)
    execution_capability: Decimal = Field(ge=0, le=100)


class DemoGrowthCase(BaseModel):
    """Validated synthetic inputs used by the deterministic demo mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: GrowthIntelligenceRequest
    revenue: RevenueSnapshot
    google_ads: AdsSnapshot
    meta_ads: AdsSnapshot

    @model_validator(mode="after")
    def validate_snapshots(self) -> "DemoGrowthCase":
        if self.google_ads.source != "google_ads":
            raise ValueError("google_ads snapshot must use the google_ads source")
        if self.meta_ads.source != "meta_ads":
            raise ValueError("meta_ads snapshot must use the meta_ads source")
        snapshots = (self.revenue, self.google_ads, self.meta_ads)
        if any(snapshot.period != self.request.period for snapshot in snapshots):
            raise ValueError("all demo snapshots must match the request period")
        # Derived metrics must be what the engine computes from the raw fixture numbers,
        # so every demo score can be reproduced (and explained) from its inputs.
        expected_momentum = revenue_momentum_score(self.revenue.growth_rate_pct)
        if self.revenue.momentum_score != expected_momentum:
            raise ValueError(
                f"revenue momentum_score {self.revenue.momentum_score} does not match the "
                f"engine value {expected_momentum}"
            )
        for snapshot in (self.google_ads, self.meta_ads):
            expected = ads_efficiency_score(
                roas=ratio(snapshot.conversion_value, snapshot.spend),
                clicks=snapshot.clicks,
                conversions=snapshot.conversions,
                target_roas=DEFAULT_TARGET_ROAS,
                target_conversion_rate_pct=DEFAULT_TARGET_CONVERSION_RATE_PCT,
            )
            if snapshot.efficiency_score != expected:
                raise ValueError(
                    f"{snapshot.source} efficiency_score {snapshot.efficiency_score} does not "
                    f"match the engine value {expected}"
                )
        return self


class GrowthIntelligenceReport(BaseModel):
    """Unified Phase 1 output for both synthetic and real provider data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_source_mode: DataSourceMode
    # Hosted self-service has no company-profile onboarding yet; never return the server's
    # example company as if it belonged to the signed-in account.
    company: CompanyProfile | None
    revenue: RevenueSnapshot
    google_ads: AdsSnapshot
    meta_ads: AdsSnapshot
    growth_score: GrowthScoreResult
    period_policy: Literal["fixture", "common_complete_calendar_days"] = "fixture"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    audit_recorded: bool = True


class GrowthSourceError(RuntimeError):
    """Founder-safe details for one or more failed Live business sources."""

    def __init__(self, failures: list[tuple[str, BaseException]]) -> None:
        self.failures = failures
        messages = [self._message(provider, error) for provider, error in failures]
        super().__init__(" ".join(messages))

    @staticmethod
    def _message(provider: str, error: BaseException) -> str:
        labels = {
            "stripe": "Stripe",
            "google_ads": "Google Ads",
            "meta_ads": "Meta Ads",
        }
        label = labels[provider]
        if isinstance(error, AccountCurrencyMismatchError):
            return f"{label}: {error}"
        if isinstance(error, AccountTimezoneError):
            return f"{label}: {error}"
        if isinstance(error, IntegrationAPIError) and error.status_code in {401, 403}:
            if provider == "google_ads":
                return (
                    "Google Ads sign-in has expired or lost permission. "
                    "Update the Google Ads OAuth settings in Connections "
                    "(a refresh token prevents hourly expiry), then load Live data again."
                )
            return (
                f"{label} rejected the saved credentials. "
                f"Update the {label} settings in Connections, then try again."
            )
        if isinstance(error, IntegrationTransportError):
            return f"{label} could not be reached. Wait a moment, then try again."
        return f"{label} could not be read. Check that connection, then try again."


class StaticRevenueSource:
    """Return a validated synthetic revenue snapshot without network access."""

    def __init__(self, snapshot: RevenueSnapshot) -> None:
        self._snapshot = snapshot

    async def collect(self, period: DateRange) -> RevenueSnapshot:
        if period != self._snapshot.period:
            raise ValueError("demo revenue period does not match the requested period")
        return self._snapshot.model_copy(deep=True)


class StaticAdsSource:
    """Return a validated synthetic ads snapshot without network access."""

    def __init__(self, snapshot: AdsSnapshot) -> None:
        self._snapshot = snapshot

    async def collect(self, period: DateRange) -> AdsSnapshot:
        if period != self._snapshot.period:
            raise ValueError("demo ads period does not match the requested period")
        return self._snapshot.model_copy(deep=True)


class GrowthIntelligenceService:
    """Collect normalized provider data and produce the deterministic V1 score."""

    def __init__(
        self,
        *,
        revenue_source: RevenueSource,
        google_ads_source: AdsSource,
        meta_ads_source: AdsSource,
        score_engine: GrowthScoreEngine | None = None,
    ) -> None:
        self._revenue_source = revenue_source
        self._google_ads_source = google_ads_source
        self._meta_ads_source = meta_ads_source
        self._score_engine = score_engine or GrowthScoreEngine()

    async def analyze(
        self,
        request: GrowthIntelligenceRequest,
        *,
        mode: DataSourceMode,
    ) -> GrowthIntelligenceReport:
        collected = await asyncio.gather(
            self._revenue_source.collect(request.period),
            self._google_ads_source.collect(request.period),
            self._meta_ads_source.collect(request.period),
            return_exceptions=True,
        )
        failures = [
            (provider, result)
            for provider, result in zip(
                ("stripe", "google_ads", "meta_ads"), collected, strict=True
            )
            if isinstance(result, BaseException)
        ]
        if failures:
            raise GrowthSourceError(failures)
        revenue = cast(RevenueSnapshot, collected[0])
        google_ads = cast(AdsSnapshot, collected[1])
        meta_ads = cast(AdsSnapshot, collected[2])
        if google_ads.currency != meta_ads.currency:
            raise ValueError(
                "live mode requires matching Google Ads and Meta Ads currencies; "
                "automatic currency conversion is not available"
            )
        inputs = GrowthScoreInputs.from_snapshots(
            revenue=revenue,
            ads=[google_ads, meta_ads],
            market_demand=request.market_demand,
            execution_capability=request.execution_capability,
        )
        return GrowthIntelligenceReport(
            data_source_mode=mode,
            company=request.company,
            revenue=revenue,
            google_ads=google_ads,
            meta_ads=meta_ads,
            growth_score=self._score_engine.score(inputs),
            period_policy=(
                "common_complete_calendar_days" if mode is DataSourceMode.LIVE else "fixture"
            ),
        )


def load_demo_case(path: Path) -> DemoGrowthCase:
    """Load a synthetic case from a repository-relative or absolute JSON path."""

    resolved = path if path.is_absolute() else REPOSITORY_ROOT / path
    return DemoGrowthCase.model_validate_json(resolved.read_text(encoding="utf-8"))


def load_company_profile(path: Path) -> CompanyProfile:
    """Load the company context used by live mode."""

    resolved = path if path.is_absolute() else REPOSITORY_ROOT / path
    return CompanyProfile.model_validate_json(resolved.read_text(encoding="utf-8"))


def validate_live_configuration(settings: Settings) -> None:
    """Reject incomplete setup before consuming provider quota or making requests."""
    required = {
        "STRIPE_SECRET_KEY": settings.stripe_secret_key,
        "GOOGLE_ADS_ACCESS_TOKEN or GOOGLE_ADS_REFRESH_TOKEN": (
            settings.google_ads_access_token
            or (
                settings.google_ads_refresh_token
                if settings.google_ads_client_id and settings.google_ads_client_secret
                else None
            )
        ),
        "GOOGLE_ADS_CUSTOMER_ID": settings.google_ads_customer_id,
        "META_ADS_ACCESS_TOKEN": settings.meta_ads_access_token,
        "META_ADS_AD_ACCOUNT_ID": settings.meta_ads_ad_account_id,
        "META_ADS_API_VERSION": settings.meta_ads_api_version,
    }
    missing: list[str] = []
    for name, value in required.items():
        getter = getattr(value, "get_secret_value", None)
        raw = getter() if getter else value
        if raw is None or not str(raw).strip():
            missing.append(name)
    if missing:
        raise ValueError(f"live mode is missing configuration: {', '.join(missing)}")
    if settings.google_ads_currency.upper() != settings.meta_ads_currency.upper():
        raise ValueError(
            "live mode requires matching Google Ads and Meta Ads currencies; "
            "automatic currency conversion is not available"
        )


def complete_reporting_period(
    *, now: datetime, days: int, reporting_timezones: tuple[str, ...]
) -> DateRange:
    """Use dates completed in every source, not an identical cross-zone instant range.

    Stripe's reporting day is UTC. Ads use their own account-local day boundaries;
    choosing the earliest local "today" keeps the common end date closed everywhere.
    A closed calendar day can still receive delayed attribution updates from providers.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("reporting time must include a timezone")
    if days < 1:
        raise ValueError("reporting days must be positive")
    zones = ["UTC", *(verified_reporting_timezone(zone) for zone in reporting_timezones)]
    end = min(now.astimezone(ZoneInfo(zone)).date() for zone in zones) - timedelta(days=1)
    return DateRange(start=end - timedelta(days=days - 1), end=end)


async def run_growth_intelligence(
    settings: Settings,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> GrowthIntelligenceReport:
    """Run one Phase 1 analysis using the configured demo or live providers."""

    mode = DataSourceMode(settings.data_source_mode)
    if mode is DataSourceMode.DEMO:
        case = load_demo_case(settings.demo_fixture_path)
        service = GrowthIntelligenceService(
            revenue_source=StaticRevenueSource(case.revenue),
            google_ads_source=StaticAdsSource(case.google_ads),
            meta_ads_source=StaticAdsSource(case.meta_ads),
        )
        return await service.analyze(case.request, mode=mode)

    validate_live_configuration(settings)
    if today is not None and now is not None:
        raise ValueError("provide either today or now, not both")
    # Preserve the deterministic calendar-date hook; it represents the as-of day,
    # not an instruction to include a still-open reporting day.
    as_of = now or (datetime.combine(today, time.min, UTC) if today else datetime.now(UTC))
    stripe_config = StripeIntegrationConfig(
        api_key=settings.stripe_secret_key,
        currency=settings.stripe_currency,
        api_base_url=settings.stripe_api_base_url,
    )
    google_config = GoogleAdsIntegrationConfig(
        access_token=settings.google_ads_access_token,
        developer_token=settings.google_ads_developer_token,
        customer_id=settings.google_ads_customer_id,
        login_customer_id=settings.google_ads_login_customer_id,
        oauth_client_id=settings.google_ads_client_id,
        oauth_client_secret=settings.google_ads_client_secret,
        oauth_refresh_token=settings.google_ads_refresh_token,
        contains_eu_political_advertising=settings.google_ads_contains_eu_political_advertising,
        api_version=settings.google_ads_api_version,
        api_base_url=settings.google_ads_api_base_url,
        currency=settings.google_ads_currency,
        target_roas=settings.google_ads_target_roas,
        target_cac=settings.google_ads_target_cac,
        target_conversion_rate_pct=settings.google_ads_target_conversion_rate_pct,
    )
    meta_config = MetaAdsIntegrationConfig(
        access_token=settings.meta_ads_access_token,
        ad_account_id=settings.meta_ads_ad_account_id,
        api_version=settings.meta_ads_api_version,
        api_base_url=settings.meta_ads_api_base_url,
        currency=settings.meta_ads_currency,
        target_roas=settings.meta_ads_target_roas,
        target_cac=settings.meta_ads_target_cac,
        target_conversion_rate_pct=settings.meta_ads_target_conversion_rate_pct,
        conversion_action_types=tuple(
            item.strip()
            for item in settings.meta_ads_conversion_action_types.split(",")
            if item.strip()
        ),
    )
    async with (
        StripeIntegration(stripe_config) as stripe,
        GoogleAdsIntegration(google_config) as google_ads,
        MetaAdsIntegration(meta_config) as meta_ads,
    ):
        metadata = await asyncio.gather(
            google_ads.verify_reporting_metadata(),
            meta_ads.verify_reporting_metadata(),
            return_exceptions=True,
        )
        failures = [
            (provider, result)
            for provider, result in zip(("google_ads", "meta_ads"), metadata, strict=True)
            if isinstance(result, BaseException)
        ]
        if failures:
            raise GrowthSourceError(failures)
        period = complete_reporting_period(
            now=as_of,
            days=settings.analysis_days,
            reporting_timezones=(cast(str, metadata[0]), cast(str, metadata[1])),
        )
        request = GrowthIntelligenceRequest(
            company=load_company_profile(settings.company_profile_path),
            period=period,
            market_demand=settings.market_demand_score,
            execution_capability=settings.execution_capability_score,
        )
        service = GrowthIntelligenceService(
            revenue_source=stripe,
            google_ads_source=google_ads,
            meta_ads_source=meta_ads,
        )
        return await service.analyze(request, mode=mode)
