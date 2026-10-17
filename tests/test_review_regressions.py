"""Regression tests for the independent review of commit 2c02a16.

Every test failed on 2c02a16 and encodes the corrected behaviour (IDs refer to the review:
P0 = demo/submission blockers, P1 = correctness). Nothing here calls an external API.
"""

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from agents.ads_agent import AdsAgent
from agents.feedback_agent import FeedbackAgent
from backend.api.app import create_app
from backend.config import Settings
from backend.models.audit_log import AgentActionLedgerEntry, AgentActionStatus
from backend.models.connectors import (
    ConnectorProvider,
    DataMode,
    FeedbackCollectionResponse,
    GeoCitation,
    GeoScanReport,
    GeoScanRequest,
)
from backend.models.feedback import FeedbackSignal, FeedbackSource
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.growth_intelligence import load_demo_case
from backend.services.growth_score import GrowthScoreEngine, GrowthScoreInputs
from backend.services.intelligence_history import IntelligenceHistoryService
from backend.services.phase2_runtime import REPOSITORY_ROOT, build_demo_runtime
from backend.services.real_intelligence import _measured_result, _mentions
from integrations.geo_providers import _vertex_citations
from integrations.google_ads import (
    GoogleAdsCampaignCreateRequest,
    GoogleAdsIntegration,
    GoogleAdsIntegrationConfig,
)
from integrations.meta_ads import MetaAdsIntegrationConfig
from integrations.metrics import ads_efficiency_score

PHASE2_FIXTURE = Path("data/demo_phase2_slowing_growth.json")
PHASE1_FIXTURE = Path("data/demo_ai_customer_support.json")
T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)


def demo_app(**settings):
    runtime = build_demo_runtime()
    return create_app(
        runtime=runtime,
        founder_experience=build_demo_founder_experience(runtime),
        settings=Settings(_env_file=None, **settings),
    )


def api_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# --- P0-1 · Demo proof on the presentation path ---------------------------------------


async def test_p0_1_demo_proof_stays_verified_after_dashboard_approval() -> None:
    """P0-1: approving the dashboard's pending campaign must not break the anchored proof."""

    async with api_client(demo_app()) as client:
        before = (await client.get("/api/v1/audit-proof/status?mode=demo")).json()
        dashboard = (await client.get("/api/v1/dashboard")).json()
        recommendation = next(
            item for item in dashboard["recommendations"] if item.get("workflow_run_id")
        )
        run = (await client.get(f"/api/v1/workflows/{recommendation['workflow_run_id']}")).json()
        decision = await client.post(
            f"/api/v1/workflows/{run['run_id']}/decision",
            json={
                "outcome": "approve",
                "actor": "founder-ui",
                "reason": "review check",
                "action_digest": run["approval"]["action_digest"],
            },
        )
        after = (await client.get("/api/v1/audit-proof/status?mode=demo")).json()

    assert before["state"] == "verified"
    assert decision.status_code == 200
    assert after["state"] == "verified", (
        f"after the founder approves, the demo proof badge becomes {after['state']!r} "
        f"({after['record_count']} records vs 3 anchored)"
    )


async def test_p0_1_demo_proof_stays_verified_after_analyze_my_business() -> None:
    """P0-1: the Agent Actions 'Analyze my business' button must not break the proof."""

    async with api_client(demo_app()) as client:
        await client.get("/api/v1/dashboard")
        started = await client.post("/api/v1/workflows/demo", json={})
        after = (await client.get("/api/v1/audit-proof/status?mode=demo")).json()

    assert started.status_code == 201
    assert after["state"] == "verified", f"proof badge became {after['state']!r}"


# --- P0-2 · Gemini is not part of the reasoning path ------------------------------------


def test_p0_2_repository_ships_a_structured_generator_implementation() -> None:
    """P0-2: some non-test class must implement generate_json (the Gemini boundary)."""

    implementations = []
    for package in ("agents", "backend", "integrations"):
        for path in (REPOSITORY_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {ast.unparse(base) for base in node.bases}
                if "Protocol" in bases:
                    continue
                if any(
                    isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name == "generate_json"
                    for item in node.body
                ):
                    implementations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{node.name}")
    assert implementations, "no StructuredGenerator implementation exists outside tests"


async def test_p0_2_founder_message_changes_the_agent_response() -> None:
    """P0-2: different founder concerns should not produce an identical diagnosis."""

    runtime = build_demo_runtime()
    churn = await runtime.orchestrator.start(
        runtime.demo_request.model_copy(
            update={"founder_message": "Our churn doubled after the pricing change."}
        )
    )
    cac = await runtime.orchestrator.start(
        runtime.demo_request.model_copy(
            update={"founder_message": "CAC is rising and AI visibility is low."}
        )
    )
    assert (churn.narrative.acknowledgement, churn.plan.recommendations) != (
        cac.narrative.acknowledgement,
        cac.plan.recommendations,
    ), "the founder message has no effect on the acknowledgement or the plan"


# --- P0-3 · Demo score must be reproducible from its own inputs -------------------------


def engine_efficiency(snapshot) -> Decimal:
    roas = snapshot.conversion_value / snapshot.spend if snapshot.spend else None
    return ads_efficiency_score(
        roas=roas,
        clicks=snapshot.clicks,
        conversions=snapshot.conversions,
        target_roas=Decimal("3.0"),
        target_conversion_rate_pct=Decimal("3.0"),
    )


def test_p0_3_demo_fixture_efficiency_matches_the_engine_formula() -> None:
    """P0-3: fixture efficiency scores must equal what the engine computes from raw data."""

    mismatches = []
    for fixture in (PHASE2_FIXTURE, PHASE1_FIXTURE):
        case = load_demo_case(fixture)
        for snapshot in (case.google_ads, case.meta_ads):
            expected = engine_efficiency(snapshot)
            if snapshot.efficiency_score != expected:
                mismatches.append(
                    f"{fixture.name} {snapshot.source}: fixture {snapshot.efficiency_score}, "
                    f"engine {expected}"
                )
    assert not mismatches, "; ".join(mismatches)


def test_p0_3_growth_score_68_is_reproducible_from_raw_inputs() -> None:
    """P0-3: recomputing the Phase 2/5 demo from raw metrics should still give 68/100."""

    case = load_demo_case(PHASE2_FIXTURE)
    ads = [
        snapshot.model_copy(update={"efficiency_score": engine_efficiency(snapshot)})
        for snapshot in (case.google_ads, case.meta_ads)
    ]
    inputs = GrowthScoreInputs.from_snapshots(
        revenue=case.revenue,
        ads=ads,
        market_demand=case.request.market_demand,
        execution_capability=case.request.execution_capability,
    )
    score = GrowthScoreEngine().score(inputs).score
    assert score == Decimal("68.0"), f"raw inputs give {score}/100, the demo shows 68/100"


# --- P0-4 / P0-5 · Google Ads execution -------------------------------------------------


async def test_p0_4_campaign_create_declares_eu_political_advertising() -> None:
    """P0-4: Google Ads rejects campaign creates without containsEuPoliticalAdvertising."""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "mutateOperationResponses": [
                    {"campaignBudgetResult": {"resourceName": "customers/1/campaignBudgets/1"}},
                    {"campaignResult": {"resourceName": "customers/1/campaigns/1"}},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    integration = GoogleAdsIntegration(
        GoogleAdsIntegrationConfig(
            access_token="test-token",
            developer_token="test-developer-token",
            customer_id="123-456-7890",
        ),
        client=client,
    )
    await integration.create_campaign(
        GoogleAdsCampaignCreateRequest(campaign_name="Review check", daily_budget=Decimal("10.00"))
    )
    await client.aclose()
    campaign = next(
        operation["campaignOperation"]["create"]
        for operation in captured["body"]["mutateOperations"]
        if "campaignOperation" in operation
    )
    assert "containsEuPoliticalAdvertising" in campaign


def test_p0_4_execution_request_carries_the_approved_ad_assets() -> None:
    """P0-4: what the founder approves (keywords, copy, URL) must reach the provider call."""

    fields = set(GoogleAdsCampaignCreateRequest.model_fields)
    missing = {"keywords", "headlines", "descriptions", "final_url"} - fields
    assert not missing, f"approved draft fields never sent to Google Ads: {sorted(missing)}"


def test_p0_5_ad_copy_does_not_contain_internal_recommendation() -> None:
    """P0-5: the agent's internal recommendation must not become customer-facing ad copy."""

    case = load_demo_case(PHASE2_FIXTURE)
    agent = AdsAgent()
    analysis = agent.analyze(case.google_ads, case.meta_ads)
    draft = agent.draft_campaign(
        case.request.company, analysis, budget=Decimal("5000.00"), end_date="2026-07-31"
    )
    ad_copy = " ".join(draft.headlines + draft.descriptions).casefold()
    assert analysis.primary_recommendation.casefold().rstrip(".") not in ad_copy, draft.descriptions


# --- P0-6 · Authentication --------------------------------------------------------------


async def test_p0_6_connection_center_requires_authentication() -> None:
    """P0-6: an anonymous caller must not be treated as the local-demo tenant."""

    async with api_client(demo_app(api_access_token="review-token-123")) as client:
        response = await client.get("/api/v1/connections")
    assert response.status_code in {401, 403}, (
        f"anonymous request → HTTP {response.status_code}, tenant "
        f"{response.json().get('tenant_id')!r}"
    )


async def test_p0_6_approval_requires_an_authenticated_approver() -> None:
    """P0-6: the approver identity must not be a free-text field any caller can fill in."""

    auth = {"Authorization": "Bearer review-token-123"}
    async with api_client(demo_app(api_access_token="review-token-123")) as client:
        dashboard = (await client.get("/api/v1/dashboard", headers=auth)).json()
        recommendation = next(
            item for item in dashboard["recommendations"] if item.get("workflow_run_id")
        )
        run = (
            await client.get(f"/api/v1/workflows/{recommendation['workflow_run_id']}", headers=auth)
        ).json()
        decision = await client.post(
            f"/api/v1/workflows/{run['run_id']}/decision",
            json={
                "outcome": "approve",
                "actor": "not-the-founder",
                "action_digest": run["approval"]["action_digest"],
            },
        )
    assert decision.status_code in {401, 403}, f"anonymous approval → HTTP {decision.status_code}"


# --- P1-7 · GEO scoring ------------------------------------------------------------------


def test_p1_7_lookalike_domain_is_not_an_owned_citation() -> None:
    """P1-7: notintercom.com must not count as an intercom.com citation."""

    result = _measured_result(
        ConnectorProvider.PERPLEXITY,
        "Best AI customer support tools?",
        "Intercom is a strong choice.",
        [GeoCitation(title="Review", url="https://notintercom.com/review")],
        "Intercom",
        [],
        "https://www.intercom.com",
        0,
    )
    assert result.brand_cited is False


def test_p1_7_gemini_grounding_redirect_can_count_as_owned_citation() -> None:
    """P1-7: Gemini/Vertex grounding URIs are redirects; the title carries the domain."""

    citations = _vertex_citations(
        {
            "groundingChunks": [
                {
                    "web": {
                        "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC123",
                        "title": "intercom.com",
                    }
                }
            ]
        }
    )
    result = _measured_result(
        ConnectorProvider.GEMINI_VERTEX,
        "Best AI customer support tools?",
        "Intercom leads the category.",
        citations,
        "Intercom",
        [],
        "https://www.intercom.com",
        0,
    )
    assert result.brand_cited is True


def test_p1_7_competitor_ada_is_not_matched_by_ada_compliance() -> None:
    """P1-7: 'ADA accessibility' is not a mention of the competitor Ada."""

    text = "Intercom is solid. Make sure the widget meets ADA accessibility requirements."
    assert not _mentions(text, "Ada")


def test_p1_7_geo_prompts_follow_the_requested_category() -> None:
    """P1-7: GeoScanRequest.category should shape the prompts; today it is ignored."""

    request = GeoScanRequest(
        brand="Ramp",
        website="https://ramp.com",
        category="corporate cards and spend management",
        competitors=["Brex"],
    )
    assert any(
        "spend" in prompt.casefold() or "corporate card" in prompt.casefold()
        for prompt in request.prompts
    ), request.prompts


# --- P1-8 · Feedback sentiment -----------------------------------------------------------


def test_p1_8_feedback_sentiment_ignores_substrings_and_negation() -> None:
    """P1-8: 'uneasy' / 'not great' must not be classified as positive."""

    analysis = FeedbackAgent().analyze(
        [FeedbackSignal(source=FeedbackSource.REDDIT, text="Setup felt uneasy and not great.")]
    )
    assert analysis.analyzed_signals[0].sentiment != "positive"


# --- P1-9 · Intelligence history ---------------------------------------------------------


def geo_report(brand: str, score: str, generated_at: datetime) -> GeoScanReport:
    return GeoScanReport(
        tenant_id="local-demo",
        mode=DataMode.LIVE,
        brand=brand,
        website="https://example.com",
        overall_visibility_score=Decimal(score),
        providers=[],
        results=[],
        provenance="review check",
        generated_at=generated_at,
    )


async def test_p1_9_unreadable_history_is_not_silently_overwritten(tmp_path) -> None:
    """P1-9: one unreadable record must not cause the whole history file to be replaced."""

    path = tmp_path / "history.json"
    path.write_text('[{"snapshot_id": "legacy", "unexpected_field": true}]', encoding="utf-8")
    service = IntelligenceHistoryService(DataMode.LIVE, path)
    response = FeedbackCollectionResponse(
        tenant_id="local-demo", mode=DataMode.LIVE, brand="Acme", sources=[], signals=[]
    )
    await service.record_feedback(response)
    preserved = list(tmp_path.glob("history.json.unreadable-*"))
    assert preserved, "the unreadable history file was overwritten instead of preserved"
    assert "legacy" in preserved[0].read_text(encoding="utf-8")
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


async def test_p1_9_history_delta_only_compares_the_same_brand(tmp_path) -> None:
    """P1-9: scanning Zendesk after Intercom is not a 60-point decline."""

    service = IntelligenceHistoryService(DataMode.LIVE, tmp_path / "history.json")
    await service.record_geo(geo_report("Intercom", "80", T0))
    await service.record_geo(geo_report("Zendesk", "20", T0 + timedelta(hours=1)))
    history = await service.list_snapshots(tenant_id="local-demo")
    zendesk = next(item for item in history.items if item.snapshot.brand == "Zendesk")
    assert zendesk.score_delta is None, f"Zendesk delta vs Intercom: {zendesk.score_delta}"


async def test_p1_9_growth_score_refresh_is_not_an_optimization_action(tmp_path) -> None:
    """P1-9: a Live dashboard refresh is a measurement, not an action behind a score change."""

    service = IntelligenceHistoryService(DataMode.LIVE, tmp_path / "history.json")
    await service.record_geo(geo_report("Intercom", "40", T0))
    await service.record_geo(geo_report("Intercom", "60", T0 + timedelta(hours=2)))
    refresh = AgentActionLedgerEntry(
        entry_id="refresh-1",
        mode=DataMode.LIVE,
        agent="Growth Intelligence Engine",
        action="Measured Growth Score 62.0/100",
        reason="Created a new growth baseline from connected revenue.",
        timestamp=T0 + timedelta(hours=1),
        status=AgentActionStatus.EXECUTED,
    )
    history = await service.list_snapshots(tenant_id="local-demo", audit_entries=[refresh])
    assert history.top_observed_effects == [], [e.action for e in history.top_observed_effects]


# --- P1-13 · Meta conversions for SaaS ---------------------------------------------------


def test_p1_13_meta_conversions_cover_saas_lead_events() -> None:
    """P1-13: SaaS accounts usually optimise for leads/sign-ups, not purchases."""

    defaults = MetaAdsIntegrationConfig.model_fields["conversion_action_types"].default
    assert any("lead" in item or "registration" in item for item in defaults), defaults


async def test_p0_1_tampered_anchored_record_is_reported_as_mismatch() -> None:
    """P0-1: changing an anchored record must still fail verification (not 'pending')."""

    runtime = build_demo_runtime()
    app = create_app(
        runtime=runtime,
        founder_experience=build_demo_founder_experience(runtime),
        settings=Settings(_env_file=None),
    )
    async with api_client(app) as client:
        await client.get("/api/v1/dashboard")
        original = runtime.audit_log._entries[0]
        runtime.audit_log._entries[0] = original.model_copy(update={"reason": "edited later"})
        status = (await client.get("/api/v1/audit-proof/status?mode=demo")).json()
    assert status["state"] == "mismatch"


async def test_dashboard_reflects_an_approval_made_in_agent_actions() -> None:
    """The dashboard and briefing must not keep showing 'pending' after approval."""

    async with api_client(demo_app()) as client:
        dashboard = (await client.get("/api/v1/dashboard")).json()
        item = next(r for r in dashboard["recommendations"] if r.get("workflow_run_id"))
        run = (await client.get(f"/api/v1/workflows/{item['workflow_run_id']}")).json()
        await client.post(
            f"/api/v1/workflows/{run['run_id']}/decision",
            json={
                "outcome": "approve",
                "actor": "founder-ui",
                "action_digest": run["approval"]["action_digest"],
            },
        )
        after = (await client.get("/api/v1/dashboard")).json()
        briefing = (await client.get("/api/v1/founder/briefing")).json()
    status = next(r for r in after["recommendations"] if r.get("workflow_run_id"))
    assert status["approval_status"] == "executed"
    assert briefing["pending_approval_id"] is None
