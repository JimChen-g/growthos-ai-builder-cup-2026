"""Fixed Phase 5 competition-demo contract."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DemoStageId(StrEnum):
    PROBLEM = "problem"
    CONNECT_DATA = "connect_data"
    DIAGNOSIS = "diagnosis"
    EXECUTION = "execution"
    FUTURE = "future"


class DemoDataProvider(StrEnum):
    STRIPE = "stripe"
    GOOGLE_ADS = "google_ads"
    META_ADS = "meta_ads"


class DemoActionKind(StrEnum):
    CREATE_CAMPAIGN = "create_campaign"
    GEO_PLAN = "geo_plan"


class DemoCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    company_name: str
    team_size: int = Field(ge=1)
    mrr: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    growth_problems: list[str] = Field(min_length=2, max_length=2)
    founder_context: str


class DemoDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: DemoDataProvider
    label: str
    signal: str
    status: Literal["connected"]


class DemoProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    evidence: str
    source: str
    severity: Literal["high", "medium"]


class DemoDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    growth_score: Decimal = Field(ge=0, le=100)
    score_band: str
    problems: list[DemoProblem] = Field(min_length=2, max_length=2)


class DemoAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DemoActionKind
    title: str
    agent: str
    output: str
    status: Literal["ready_for_approval", "plan_ready"]
    guardrail: str


class DemoFutureConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Literal["Growth Passport", "Growth Capital"]
    description: str
    availability: Literal["future"]


class DemoStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: DemoStageId
    label: str
    title: str
    start_seconds: int = Field(ge=0, lt=180)
    end_seconds: int = Field(gt=0, le=180)
    objective: str
    presenter_cue: str
    proof_points: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_range(self) -> "DemoStage":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("stage end must be after its start")
        return self


class DemoScript(BaseModel):
    """One deterministic case and one exact 180-second presentation path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    script_id: str
    fixed_case: Literal[True]
    total_duration_seconds: Literal[180]
    case: DemoCase
    data_sources: list[DemoDataSource] = Field(min_length=3, max_length=3)
    diagnosis: DemoDiagnosis
    actions: list[DemoAction] = Field(min_length=2, max_length=2)
    future: list[DemoFutureConcept] = Field(min_length=2, max_length=2)
    stages: list[DemoStage] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def freeze_phase5_scope(self) -> "DemoScript":
        expected_ranges = [
            (DemoStageId.PROBLEM, 0, 30),
            (DemoStageId.CONNECT_DATA, 30, 60),
            (DemoStageId.DIAGNOSIS, 60, 120),
            (DemoStageId.EXECUTION, 120, 160),
            (DemoStageId.FUTURE, 160, 180),
        ]
        actual_ranges = [
            (stage.id, stage.start_seconds, stage.end_seconds) for stage in self.stages
        ]
        if actual_ranges != expected_ranges:
            raise ValueError("Phase 5 stages must match the fixed three-minute rundown")
        if self.case.title != "AI Customer Support Startup":
            raise ValueError("Phase 5 uses the fixed AI Customer Support Startup case")
        if self.case.team_size != 5 or self.case.mrr != Decimal("30000"):
            raise ValueError("Phase 5 case must keep Team 5 and MRR $30k")
        if self.case.growth_problems != ["CAC rising", "AI visibility low"]:
            raise ValueError("Phase 5 growth problems are fixed")
        if [source.provider for source in self.data_sources] != [
            DemoDataProvider.STRIPE,
            DemoDataProvider.GOOGLE_ADS,
            DemoDataProvider.META_ADS,
        ]:
            raise ValueError("Phase 5 data sources must be Stripe, Google Ads, and Meta Ads")
        if [action.kind for action in self.actions] != [
            DemoActionKind.CREATE_CAMPAIGN,
            DemoActionKind.GEO_PLAN,
        ]:
            raise ValueError("Phase 5 actions must be Create Campaign then GEO Plan")
        if [concept.title for concept in self.future] != [
            "Growth Passport",
            "Growth Capital",
        ]:
            raise ValueError("Phase 5 future concepts are fixed")
        return self
