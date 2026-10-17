from decimal import Decimal

import pytest

from agents.gemini_growth_advisor import GeminiGrowthAdvisor
from backend.models.company import CompanyProfile
from backend.services.growth_score import GrowthScoreEngine, GrowthScoreInputs


def test_growth_score_v1_matches_frozen_weighting() -> None:
    result = GrowthScoreEngine().score(
        GrowthScoreInputs(
            revenue_momentum=70,
            marketing_efficiency=60,
            market_demand=80,
            execution_capability=65,
        )
    )

    assert result.score == Decimal("68.0")
    assert result.band == "promising"
    assert result.contributions == {
        "revenue_momentum": Decimal("21.0"),
        "marketing_efficiency": Decimal("18.0"),
        "market_demand": Decimal("16.0"),
        "execution_capability": Decimal("13.0"),
    }


class FakeStructuredGenerator:
    def __init__(self) -> None:
        self.prompt = ""

    async def generate_json(self, *, prompt: str, response_schema: dict) -> dict:
        self.prompt = prompt
        assert response_schema["title"] == "GrowthScoreNarrative"
        return {
            "executive_summary": "Growth is promising, with marketing as the clearest gap.",
            "opportunities": ["Improve paid acquisition efficiency."],
            "risks": ["Marketing efficiency trails other components."],
            "recommended_next_step": "Review high-CAC campaigns before increasing spend.",
            "referenced_components": ["marketing_efficiency", "revenue_momentum"],
        }


@pytest.mark.asyncio
async def test_gemini_boundary_narrates_but_does_not_change_score() -> None:
    result = GrowthScoreEngine().score(
        GrowthScoreInputs(
            revenue_momentum=70,
            marketing_efficiency=60,
            market_demand=80,
            execution_capability=65,
        )
    )
    company = CompanyProfile(
        company_name="Acme AI",
        industry="B2B SaaS",
        website="https://example.com",
        target_market="Seed-stage software teams",
        stage="seed",
        monthly_revenue="25000",
    )
    generator = FakeStructuredGenerator()

    narrative = await GeminiGrowthAdvisor(generator).advise(company, result)

    assert narrative.referenced_components == ["marketing_efficiency", "revenue_momentum"]
    assert '"score":"68.0"' in generator.prompt.replace(" ", "")
    assert result.score == Decimal("68.0")
