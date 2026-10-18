from datetime import date
from decimal import Decimal

import pytest

import backend.services.growth_intelligence as growth_intelligence
from backend.config import Settings
from backend.services.growth_intelligence import (
    DataSourceMode,
    load_demo_case,
    run_growth_intelligence,
)
from demo.run_phase1 import format_report


@pytest.mark.asyncio
async def test_demo_case_meets_phase1_acceptance_output() -> None:
    report = await run_growth_intelligence(Settings(_env_file=None, data_source_mode="demo"))

    assert report.data_source_mode is DataSourceMode.DEMO
    assert report.company.company_name == "AI Customer Support Agent"
    assert report.company.monthly_revenue == Decimal("30000.00")
    assert report.revenue.mrr == Decimal("30000.00")
    assert report.google_ads.roas == Decimal("2.8")
    assert report.meta_ads.cac == Decimal("120.0")
    assert report.growth_score.score == Decimal("68.0")
    assert report.growth_score.components["revenue_momentum"] == Decimal("80.0")
    assert report.growth_score.components["marketing_efficiency"] == Decimal("93.0")

    output = format_report(report)
    assert "Company:\nAI Customer Support Agent" in output
    assert "MRR:\n30000 USD" in output
    assert "Google Ads ROAS:\n2.8" in output
    assert "Meta CAC:\n120 USD" in output
    assert "Growth Score:\n68/100" in output
    assert "Revenue Momentum:\n80" in output
    assert "Marketing Efficiency:\n93" in output
    assert "Data Source:\ndemo" in output


@pytest.mark.asyncio
async def test_live_mode_requires_provider_credentials() -> None:
    with pytest.raises(ValueError, match="live mode is missing configuration"):
        await run_growth_intelligence(Settings(_env_file=None, data_source_mode="live"))


class FakeLiveSource:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot

    async def __aenter__(self) -> "FakeLiveSource":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def collect(self, period):
        return self.snapshot.model_copy(update={"period": period}, deep=True)

    async def verify_reporting_metadata(self):
        return "UTC"


@pytest.mark.asyncio
async def test_live_mode_reuses_the_same_aggregation_contract(monkeypatch) -> None:
    case = load_demo_case(Settings(_env_file=None).demo_fixture_path)
    monkeypatch.setattr(
        growth_intelligence,
        "StripeIntegration",
        lambda _config: FakeLiveSource(case.revenue),
    )
    monkeypatch.setattr(
        growth_intelligence,
        "GoogleAdsIntegration",
        lambda _config: FakeLiveSource(case.google_ads),
    )
    monkeypatch.setattr(
        growth_intelligence,
        "MetaAdsIntegration",
        lambda _config: FakeLiveSource(case.meta_ads),
    )
    settings = Settings(
        _env_file=None,
        data_source_mode="live",
        analysis_days=31,
        # Same assessed inputs as the demo fixture, so the same raw data gives the same score.
        market_demand_score=case.request.market_demand,
        execution_capability_score=case.request.execution_capability,
        stripe_secret_key="stripe_test",
        google_ads_access_token="google_oauth",
        google_ads_developer_token="google_developer",
        google_ads_customer_id="1234567890",
        meta_ads_access_token="meta_token",
        meta_ads_ad_account_id="act_1234567890",
        meta_ads_api_version="v26.0",
    )

    report = await run_growth_intelligence(settings, today=date(2026, 7, 31))

    assert report.data_source_mode is DataSourceMode.LIVE
    assert report.revenue.mrr == Decimal("30000.00")
    assert report.google_ads.roas == Decimal("2.8")
    assert report.meta_ads.cac == Decimal("120.0")
    assert report.growth_score.score == Decimal("68.0")
