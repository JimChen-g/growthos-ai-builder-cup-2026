"""Founder-facing explanation contract for the Phase 2 demo."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class NarrativeProblem(BaseModel):
    """One prioritized, evidence-backed growth problem."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    problem: str
    reason: str
    evidence: list[str] = Field(min_length=1)


class ExpectedImpact(BaseModel):
    """Forecast shown as an expectation, never as a guaranteed outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_roas: Decimal = Field(gt=0)
    budget: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    measurement_window_days: int = Field(default=7, ge=1, le=90)
    disclaimer: str


class FounderAgentResponse(BaseModel):
    """Stable story contract consumed by the CLI and Founder UI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    founder_message: str = Field(min_length=1, max_length=500)
    acknowledgement: str
    top_problems: list[NarrativeProblem] = Field(min_length=1, max_length=3)
    recommended_actions: list[str] = Field(min_length=1, max_length=5)
    expected_impact: ExpectedImpact
    approval_prompt: str
