"""Deterministic Phase 1 metric rules shared by provider adapters."""

from decimal import ROUND_HALF_UP, Decimal

from integrations.models import CampaignPerformance

TENTH = Decimal("0.1")
HUNDRED = Decimal("100")
DEFAULT_TARGET_ROAS = Decimal("3.0")
DEFAULT_TARGET_CONVERSION_RATE_PCT = Decimal("3.0")


def decimal_value(value: object | None) -> Decimal:
    """Convert provider JSON numbers or strings without binary float drift."""

    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def rounded(value: Decimal) -> Decimal:
    return value.quantize(TENTH, rounding=ROUND_HALF_UP)


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return rounded(((current - previous) / previous) * HUNDRED)


def clamp_score(value: Decimal) -> Decimal:
    return rounded(max(Decimal("0"), min(HUNDRED, value)))


def revenue_momentum_score(growth_rate_pct: Decimal | None) -> Decimal:
    """Map -50%..+50% period growth to 0..100; unknown growth is neutral."""

    if growth_rate_pct is None:
        return Decimal("50.0")
    return clamp_score(Decimal("50") + growth_rate_pct)


def campaign_performance(
    *,
    campaign_id: str,
    campaign_name: str,
    status: str | None,
    spend: Decimal,
    clicks: int,
    conversions: Decimal,
    conversion_value: Decimal,
) -> CampaignPerformance:
    return CampaignPerformance(
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        status=status,
        spend=spend,
        clicks=clicks,
        conversions=conversions,
        conversion_value=conversion_value,
        roas=rounded(value) if (value := ratio(conversion_value, spend)) is not None else None,
        cac=rounded(value) if (value := ratio(spend, conversions)) is not None else None,
    )


def ads_efficiency_score(
    *,
    roas: Decimal | None,
    clicks: int,
    conversions: Decimal,
    target_roas: Decimal,
    target_conversion_rate_pct: Decimal,
) -> Decimal:
    """Blend ROAS (70%) and conversion rate (30%) against configured targets."""

    roas_score = Decimal("0")
    if roas is not None and target_roas > 0:
        roas_score = clamp_score(roas / target_roas * HUNDRED)

    conversion_rate_score = Decimal("0")
    if clicks > 0 and target_conversion_rate_pct > 0:
        conversion_rate_pct = conversions / Decimal(clicks) * HUNDRED
        conversion_rate_score = clamp_score(
            conversion_rate_pct / target_conversion_rate_pct * HUNDRED
        )

    return clamp_score(roas_score * Decimal("0.70") + conversion_rate_score * Decimal("0.30"))


def diagnose_ads_health(
    *,
    spend: Decimal,
    conversions: Decimal,
    roas: Decimal | None,
    cac: Decimal | None,
    trend_pct: Decimal | None,
    target_roas: Decimal,
    target_cac: Decimal | None,
) -> list[str]:
    problems: list[str] = []
    if spend > 0 and conversions == 0:
        problems.append("No conversions")
    if roas is not None and roas < target_roas:
        problems.append("ROAS below target")
    if target_cac is not None and cac is not None and cac > target_cac:
        problems.append("High CAC")
    if trend_pct is not None and trend_pct <= Decimal("-10"):
        problems.append("ROAS declining")
    return problems
