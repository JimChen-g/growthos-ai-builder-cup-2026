from decimal import Decimal

from agents.ads_agent import AdsAgent
from agents.geo_agent import AIVisibilityObservation, GEOAgent
from backend.config import Settings
from backend.services.growth_intelligence import load_demo_case


def test_ads_agent_analyzes_and_drafts_without_side_effects() -> None:
    case = load_demo_case(Settings(_env_file=None).demo_fixture_path)
    agent = AdsAgent(target_roas=Decimal("3.0"))

    analysis = agent.analyze(case.google_ads, case.meta_ads)
    draft = agent.draft_campaign(
        case.request.company,
        analysis,
        budget=Decimal("5000"),
        end_date="2026-07-31",
    )

    assert analysis.primary_problem == "ROAS is below target"
    assert analysis.primary_recommendation == "Launch a tightly scoped search campaign test"
    assert draft.total_budget == Decimal("5000")
    assert draft.daily_budget == Decimal("166.67")
    assert draft.expected_kpi.roas == Decimal("3.2")
    assert draft.campaign_name.endswith("2026-07-31")
    assert draft.keywords


def test_ads_agent_grounds_creative_fatigue_in_cac_and_roas_trends() -> None:
    settings = Settings(_env_file=None)
    case = load_demo_case(settings.phase2_demo_fixture_path)

    analysis = AdsAgent().analyze(case.google_ads, case.meta_ads)

    assert analysis.primary_problem == "CAC increased"
    assert analysis.primary_reason == "Creative fatigue"
    assert analysis.primary_recommendation == "Create a new campaign with refreshed creative"
    primary = next(issue for issue in analysis.issues if issue.reason == "Creative fatigue")
    assert "CAC increased 50.0% versus the prior period" in primary.evidence
    assert "ROAS declined 15.0% versus the prior period" in primary.evidence


def test_geo_agent_reports_missing_brand_competitors_and_action_plan() -> None:
    result = GEOAgent().analyze(
        "Acme AI",
        [
            AIVisibilityObservation(prompt="Best AI support tools", cited_brands=["Alpha", "Beta"]),
            AIVisibilityObservation(
                prompt="AI support comparison", cited_brands=["Alpha", "Gamma"]
            ),
        ],
    )

    assert result.brand_status == "Missing"
    assert result.visibility_score == 0
    assert [item.brand for item in result.competitors] == ["Alpha", "Beta", "Gamma"]
    assert result.competitors[0].share_of_prompts_pct == 100
    assert len(result.action_plan) == 3
