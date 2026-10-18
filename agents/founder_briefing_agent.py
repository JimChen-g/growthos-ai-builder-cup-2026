"""Grounded Founder Daily Briefing narration with an optional Gemini boundary."""

import logging
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class StructuredBriefingGenerator(Protocol):
    async def generate_json(
        self, *, prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]: ...


class BriefingNarrativeContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    company_name: str
    growth_score: str
    growth_band: str
    revenue_change: str
    google_roas_change: str
    ai_visibility_change: str
    risk: str
    recommendation: str
    revenue_is_period_total: bool = False


class FounderBriefingNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executive_summary: str = Field(min_length=1, max_length=500)
    recommendation_reason: str = Field(min_length=1, max_length=400)


class FounderBriefingAgent:
    """Narrate supplied facts without changing metrics or inventing performance."""

    def __init__(self, generator: StructuredBriefingGenerator | None = None) -> None:
        self._generator = generator

    async def narrate(self, context: BriefingNarrativeContext) -> FounderBriefingNarrative:
        fallback = self._deterministic(context)
        if self._generator is None:
            return fallback

        prompt = (
            "Write a concise daily briefing for a startup founder. Use only the supplied facts. "
            "Do not change a number, promise an outcome, or add an unsupported cause. Return JSON "
            "matching the response schema.\n\n"
            f"Grounded facts:\n{context.model_dump_json(indent=2)}"
        )
        try:
            payload = await self._generator.generate_json(
                prompt=prompt,
                response_schema=FounderBriefingNarrative.model_json_schema(),
            )
            return FounderBriefingNarrative.model_validate(payload)
        except Exception:  # noqa: BLE001 - narration is optional; the briefing must render
            logger.warning(
                "Gemini briefing narration failed; using grounded template", exc_info=True
            )
            return fallback

    @staticmethod
    def _deterministic(context: BriefingNarrativeContext) -> FounderBriefingNarrative:
        change = context.revenue_change.strip()
        if change.startswith("-"):
            momentum = f"Revenue is down {change.lstrip('-')}"
        elif change.lstrip("+").rstrip("%") in {"0", "0.0"}:
            momentum = "Revenue is flat"
        else:
            momentum = f"Revenue is up {change.lstrip('+')}"
        if context.revenue_is_period_total:
            momentum = momentum.replace("Revenue", "Period revenue", 1)
            momentum += " vs. prior reporting period"
        return FounderBriefingNarrative(
            executive_summary=(
                f"Growth health is {context.growth_score} ({context.growth_band}). "
                f"{momentum}, while {context.risk} remains the main risk."
            ),
            recommendation_reason=(
                f"Prioritize {context.recommendation.lower()} to address the clearest "
                "unowned growth opportunity."
            ),
        )
