"""Gemini interface boundary reserved for structured score narration.

The V1 numeric score remains deterministic. This adapter can ask a future Gemini
client to explain the already-computed result, but it cannot change that result or
execute external actions.
"""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.models.company import CompanyProfile
from backend.services.growth_score import GrowthScoreResult


class StructuredGenerator(Protocol):
    """Small boundary to be implemented by the chosen Gemini SDK in a later phase."""

    async def generate_json(
        self, *, prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]: ...


class GrowthScoreNarrative(BaseModel):
    """Validated narrative returned by the Gemini boundary."""

    model_config = ConfigDict(extra="forbid")

    executive_summary: str = Field(min_length=1, max_length=600)
    opportunities: list[str] = Field(min_length=1, max_length=3)
    risks: list[str] = Field(max_length=3)
    recommended_next_step: str = Field(min_length=1, max_length=300)
    referenced_components: list[str] = Field(min_length=1, max_length=4)


class GeminiGrowthAdvisor:
    """Generate grounded narration through an injected structured Gemini client."""

    def __init__(self, generator: StructuredGenerator) -> None:
        self._generator = generator

    async def advise(
        self, company: CompanyProfile, result: GrowthScoreResult
    ) -> GrowthScoreNarrative:
        prompt = (
            "Explain this deterministic Growth Score without changing any number. "
            "Use only the supplied company profile, components, weights, and contributions. "
            "If evidence is missing, state the limitation. Return JSON matching the schema.\n\n"
            f"Company profile:\n{company.model_dump_json(indent=2)}\n\n"
            f"Growth score:\n{result.model_dump_json(indent=2)}"
        )
        payload = await self._generator.generate_json(
            prompt=prompt,
            response_schema=GrowthScoreNarrative.model_json_schema(),
        )
        return GrowthScoreNarrative.model_validate(payload)
