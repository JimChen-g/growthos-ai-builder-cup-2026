"""No-data Live reports must not turn absent observations into poor performance."""

from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from backend.api.app import create_app
from backend.config import Settings
from backend.models.approval import ApprovalDecision, ApprovalOutcome, ApprovalStatus
from backend.services.growth_intelligence import (
    DataSourceMode,
    GrowthIntelligenceService,
    StaticAdsSource,
    StaticRevenueSource,
    load_demo_case,
    run_growth_intelligence,
)
from backend.services.growth_score import (
    GrowthScoreEngine,
    GrowthScoreInputs,
    GrowthScoreResult,
    InsufficientGrowthDataError,
)
from backend.services.phase2_runtime import build_demo_runtime
from demo.run_phase1 import format_report
from integrations.models import AdsSnapshot, DateRange, RevenueSnapshot

PERIOD = DateRange(start="2026-09-18", end="2026-10-17")


def revenue(growth_rate=Decimal("10"), amount=Decimal("100")):
    return RevenueSnapshot(
        period=PERIOD, currency="USD", revenue=amount, customers=2, mrr=50,
        growth_rate_pct=growth_rate, momentum_score=50 if growth_rate is None else 60,
    )


def ads(source, spend=Decimal("0"), efficiency=Decimal("0")):
    return AdsSnapshot(
        source=source, period=PERIOD, currency="USD", campaigns=[], spend=spend,
        clicks=0, conversions=0, conversion_value=0, efficiency_score=efficiency,
    )


def score(revenue_snapshot, ads_snapshots, *, strict=True):
    inputs = GrowthScoreInputs.from_snapshots(
        revenue=revenue_snapshot, ads=ads_snapshots,
        market_demand=Decimal("60"), execution_capability=Decimal("62.5"),
        require_observed_data=strict,
    )
    return GrowthScoreEngine().score(inputs)


@pytest.mark.parametrize("snapshots", [[], [ads("google_ads")], [
    ads("google_ads", efficiency=Decimal("100")), ads("meta_ads")
]])
def test_missing_ad_spend_is_unavailable_even_with_revenue_or_legacy_efficiency(snapshots):
    result = score(revenue(), snapshots)
    assert result.status == "insufficient_data"
    assert result.score is None and result.band is None
    assert result.missing_components == ["marketing_efficiency"]
    assert result.components["marketing_efficiency"] is None
    assert result.contributions["marketing_efficiency"] is None
    assert result.components["revenue_momentum"] == 60
    payload = result.model_dump(mode="json")
    assert payload["score"] is None and payload["band"] is None
    assert "at_risk" not in result.model_dump_json()


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("100")])
def test_missing_revenue_comparison_is_not_a_measured_neutral_score(amount):
    result = score(revenue(None, amount), [ads("google_ads", Decimal("100"), Decimal("80"))])
    assert result.status == "insufficient_data"
    assert result.score is None and result.band is None
    assert result.missing_components == ["revenue_momentum"]
    assert result.components["revenue_momentum"] is None
    assert result.contributions["revenue_momentum"] is None
    assert result.components["marketing_efficiency"] == 80


def test_completely_empty_sources_preserve_raw_zero_but_do_not_assign_risk():
    result = score(revenue(None, Decimal("0")), [ads("google_ads"), ads("meta_ads")])
    assert result.status == "insufficient_data"
    assert result.missing_components == ["revenue_momentum", "marketing_efficiency"]
    assert result.score is None and result.band is None
    # The assessed components retain their disclosed weights; they cannot manufacture
    # an overall score by reweighting away the missing observed components.
    assert result.weights["market_demand"] == Decimal("0.20")
    assert result.contributions["market_demand"] == 12


@pytest.mark.parametrize("empty_first", [False, True])
def test_one_spending_source_is_sufficient_and_empty_source_cannot_drag_score_down(empty_first):
    snapshots = [ads("google_ads", Decimal("100"), Decimal("80")), ads("meta_ads")]
    if empty_first:
        snapshots.reverse()
    result = score(revenue(), snapshots)
    assert result.status == "available" and result.missing_components == []
    assert result.components["marketing_efficiency"] == 80
    assert result.score == Decimal("66.5") and result.band == "promising"


def test_measured_zero_is_still_a_valid_score_and_not_confused_with_absence():
    result = GrowthScoreEngine().score(GrowthScoreInputs(
        revenue_momentum=0, marketing_efficiency=0, market_demand=0, execution_capability=0,
    ))
    assert result.status == "available"
    assert result.score == 0 and result.band == "at_risk"
    assert result.missing_components == []


def test_actual_revenue_decline_and_spend_without_returns_can_still_be_at_risk():
    observed_decline = revenue().model_copy(update={
        "revenue": Decimal("0"), "growth_rate_pct": Decimal("-100"),
        "momentum_score": Decimal("0"),
    })
    result = score(observed_decline, [ads("google_ads", Decimal("100"))])
    assert result.status == "available"
    assert result.components["revenue_momentum"] == 0
    assert result.components["marketing_efficiency"] == 0
    assert result.score == Decimal("24.5") and result.band == "at_risk"


@pytest.mark.parametrize("updates", [
    {"score": "0"}, {"band": "at_risk"}, {"status": "available"},
    {"missing_components": []},
])
def test_unavailable_contract_rejects_numeric_score_or_false_availability(updates):
    result = score(revenue(), [ads("google_ads")])
    with pytest.raises(ValidationError):
        GrowthScoreResult.model_validate({**result.model_dump(), **updates})


@pytest.mark.asyncio
async def test_service_applies_observation_requirement_to_live_without_changing_sample():
    case = load_demo_case(Settings(_env_file=None).demo_fixture_path)
    request = case.request.model_copy(update={"period": PERIOD})
    service = GrowthIntelligenceService(
        revenue_source=StaticRevenueSource(revenue(None, Decimal("0"))),
        google_ads_source=StaticAdsSource(ads("google_ads")),
        meta_ads_source=StaticAdsSource(ads("meta_ads")),
    )
    report = await service.analyze(request, mode=DataSourceMode.LIVE)
    assert report.growth_score.status == "insufficient_data"
    assert report.revenue.revenue == 0 and report.google_ads.spend == 0
    assert report.data_source_mode is DataSourceMode.LIVE
    sample = await run_growth_intelligence(Settings(_env_file=None, data_source_mode="demo"))
    assert sample.data_source_mode is DataSourceMode.DEMO
    assert sample.growth_score.status == "available"
    assert sample.growth_score.score == Decimal("68.0")
    assert sample.growth_score.band == "promising"


async def unavailable_report():
    report = await run_growth_intelligence(Settings(_env_file=None, data_source_mode="demo"))
    return report.model_copy(update={
        "data_source_mode": DataSourceMode.LIVE,
        "growth_score": score(revenue(None, Decimal("0")), [ads("google_ads")]),
    })


@pytest.mark.asyncio
async def test_phase1_live_cli_handles_missing_scores_components_and_nullable_metrics():
    report = await unavailable_report()
    report = report.model_copy(update={
        "company": None,
        "revenue": report.revenue.model_copy(update={"mrr": None}),
        "google_ads": report.google_ads.model_copy(update={"roas": None}),
        "meta_ads": report.meta_ads.model_copy(update={"cac": None}),
    })
    rendered = format_report(report)
    assert "Growth Score:\nUnavailable (insufficient data)" in rendered
    assert "Revenue Momentum:\nUnavailable" in rendered
    assert "Marketing Efficiency:\nUnavailable" in rendered
    assert "Google Ads ROAS:\nUnavailable" in rendered
    assert "None" not in rendered and "0/100" not in rendered
    assert "Data Source:\nlive" in rendered


@pytest.mark.asyncio
async def test_direct_live_workflow_stops_before_agents_approval_or_execution(monkeypatch):
    runtime = build_demo_runtime()
    report = await unavailable_report()

    async def collect(_request, *, mode):
        assert mode is DataSourceMode.LIVE
        return report

    monkeypatch.setattr(runtime.orchestrator._intelligence, "analyze", collect)
    request = runtime.demo_request.model_copy(update={"data_source_mode": DataSourceMode.LIVE})
    with pytest.raises(InsufficientGrowthDataError, match="insufficient for a decision"):
        await runtime.orchestrator.start(request)
    assert runtime.writer.calls == []
    assert await runtime.approvals.list() == []
    assert await runtime.audit_log.all_entries() == []
    assert runtime.orchestrator._runs == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("method, endpoint", [
    ("POST", "/api/v1/workflows/demo"),
    ("GET", "/api/v1/founder/briefing"),
    ("GET", "/api/v1/dashboard"),
    ("GET", "/api/v1/founder/experience"),
])
async def test_legacy_numeric_consumers_return_safe_conflict_for_unavailable_report(
    monkeypatch, method, endpoint
):
    runtime = build_demo_runtime()
    report = await unavailable_report()

    async def collect(_request, *, mode):
        # HTTP legacy routes still use the fixed demo request; this injected unavailable
        # output checks the safety boundary without introducing a new Live workflow API.
        assert mode is DataSourceMode.DEMO
        return report

    monkeypatch.setattr(runtime.orchestrator._intelligence, "analyze", collect)
    app = create_app(runtime, settings=Settings(_env_file=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(method, endpoint)
    assert response.status_code == 409
    assert response.json() == {"detail": str(InsufficientGrowthDataError())}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "None/100" not in response.text
    assert runtime.writer.calls == []
    assert await runtime.approvals.list() == []


@pytest.mark.asyncio
async def test_unavailable_resumed_baseline_is_rejected_before_approval_mutates():
    runtime = build_demo_runtime()
    pending = await runtime.orchestrator.start(runtime.demo_request)
    before = await runtime.audit_log.all_entries()
    unavailable = await unavailable_report()
    runtime.orchestrator._runs[pending.run_id]["report"] = unavailable
    with pytest.raises(InsufficientGrowthDataError):
        await runtime.orchestrator.resume(pending.run_id, ApprovalDecision(
            outcome=ApprovalOutcome.APPROVE, actor="offline reviewer",
            action_digest=pending.approval.action_digest,
        ))
    approval = await runtime.approvals.get(pending.approval.approval_id)
    assert approval.status is ApprovalStatus.PENDING
    assert await runtime.audit_log.all_entries() == before
    assert runtime.writer.calls == []
