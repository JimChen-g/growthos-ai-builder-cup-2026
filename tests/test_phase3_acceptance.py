from decimal import Decimal

import httpx
import pytest

from agents.feedback_agent import FeedbackAgent
from agents.founder_briefing_agent import (
    BriefingNarrativeContext,
    FounderBriefingAgent,
)
from backend.api.app import create_app
from backend.models.connectors import DataMode
from backend.models.feedback import FeedbackSignal
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


def test_feedback_agent_turns_reddit_and_x_into_customer_intelligence() -> None:
    result = FeedbackAgent().analyze(
        [
            FeedbackSignal(source="reddit", text="The automation is excellent and fast."),
            FeedbackSignal(source="x", text="A reliable and helpful agent workflow."),
            FeedbackSignal(source="reddit", text="Onboarding is confusing and hard."),
        ]
    )

    assert result.sample_count == 3
    assert result.source_counts == {"reddit": 2, "x": 1}
    assert result.main_problem == "Onboarding difficulty"
    assert result.problems[0].impact == "high"
    assert "guided setup checklist" in result.recommendation
    assert result.analyzed_signals[-1].sentiment == "negative"


class FakeBriefingGenerator:
    def __init__(self) -> None:
        self.prompt = ""

    async def generate_json(self, *, prompt: str, response_schema: dict) -> dict:
        self.prompt = prompt
        assert response_schema["title"] == "FounderBriefingNarrative"
        return {
            "executive_summary": "Growth is healthy, with one paid-acquisition risk to watch.",
            "recommendation_reason": "Improve the missing answer-engine coverage next.",
        }


@pytest.mark.asyncio
async def test_briefing_agent_grounds_optional_structured_reasoning() -> None:
    generator = FakeBriefingGenerator()
    agent = FounderBriefingAgent(generator)
    context = BriefingNarrativeContext(
        company_name="Acme AI",
        growth_score="68/100",
        growth_band="promising",
        revenue_change="+12%",
        google_roas_change="+20%",
        ai_visibility_change="+8%",
        risk="Meta Ads CAC increased",
        recommendation="Improve AI Search Presence",
    )

    narrative = await agent.narrate(context)

    assert narrative.executive_summary.startswith("Growth is healthy")
    assert '"growth_score":"68/100"' in generator.prompt.replace(" ", "")
    assert "Do not change a number" in generator.prompt


@pytest.mark.asyncio
async def test_phase3_service_builds_briefing_dashboard_and_feedback_from_phase2() -> None:
    runtime = build_demo_runtime()
    service = build_demo_founder_experience(runtime)

    experience = await service.experience()

    assert experience.briefing.salutation == "Good Morning, Founder"
    assert experience.briefing.growth_health == Decimal("68.0")
    assert [item.key for item in experience.briefing.yesterday] == [
        "revenue",
        "google_ads_roas",
        "ai_visibility",
    ]
    assert [item.change_pct for item in experience.briefing.yesterday] == [
        Decimal("12.0"),
        Decimal("20.0"),
        Decimal("8.0"),
    ]
    assert experience.briefing.top_recommendation == "Improve AI Search Presence"
    assert experience.briefing.pending_approval_id is not None
    assert experience.briefing.provenance.mode is DataMode.DEMO
    assert all(signal.provenance.mode is DataMode.DEMO for signal in experience.briefing.yesterday)

    metrics = {item.key: item for item in experience.dashboard.metrics}
    assert set(metrics) == {
        "growth_score",
        "revenue",
        "marketing_roi",
        "ai_visibility",
        "customer_sentiment",
    }
    assert metrics["growth_score"].value == Decimal("68.0")
    assert metrics["revenue"].value == Decimal("30000.00")
    assert metrics["marketing_roi"].value == Decimal("2.4")
    assert metrics["ai_visibility"].value == Decimal("40.0")
    assert metrics["customer_sentiment"].value == Decimal("78.0")
    assert all(metric.provenance.mode is DataMode.DEMO for metric in metrics.values())
    assert experience.dashboard.customer_intelligence.main_problem == "Onboarding difficulty"
    assert experience.dashboard.customer_intelligence_provenance.mode is DataMode.DEMO
    assert all(
        item.provenance.mode is DataMode.DEMO for item in experience.dashboard.recommendations
    )
    assert experience.dashboard.recommendations[1].approval_status == "pending"
    assert experience.dashboard.recommendations[1].workflow_run_id is not None
    assert runtime.writer.calls == []


@pytest.mark.asyncio
async def test_phase3_api_exposes_all_founder_experience_routes() -> None:
    runtime = build_demo_runtime()
    service = build_demo_founder_experience(runtime)
    app = create_app(runtime, service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        briefing = await client.get("/api/v1/founder/briefing")
        dashboard = await client.get("/api/v1/dashboard")
        dashboard_payload = dashboard.json()
        ads_recommendation = dashboard_payload["recommendations"][1]
        linked_workflow = await client.get(
            f"/api/v1/workflows/{ads_recommendation['workflow_run_id']}"
        )
        feedback = await client.get("/api/v1/feedback/demo")
        custom_feedback = await client.post(
            "/api/v1/feedback/analyze",
            json={
                "signals": [
                    {"source": "reddit", "text": "Setup is confusing."},
                    {"source": "x", "text": "The automation is great and fast."},
                ]
            },
        )

    assert briefing.status_code == 200
    assert briefing.json()["growth_health"] == "68.0"
    assert briefing.json()["provenance"]["mode"] == "demo"
    assert dashboard.status_code == 200
    assert len(dashboard_payload["metrics"]) == 5
    assert {item["provenance"]["mode"] for item in dashboard_payload["metrics"]} == {"demo"}
    assert linked_workflow.status_code == 200
    assert linked_workflow.json()["approval"]["approval_id"] == ads_recommendation["approval_id"]
    assert feedback.status_code == 200
    assert feedback.json()["main_problem"] == "Onboarding difficulty"
    assert custom_feedback.status_code == 200
    assert custom_feedback.json()["main_problem"] == "Onboarding difficulty"
