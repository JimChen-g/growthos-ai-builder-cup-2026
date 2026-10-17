"""Contracts for the public, evidence-bound Builder Cup workflow."""

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DecisionSource(StrEnum):
    LIVE_VERTEX = "live_vertex"
    CACHE = "cache"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class EvidenceItem(BaseModel):
    """One canonical business fact; the model can cite its id but cannot rewrite its value."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    evidence_id: str = Field(pattern=r"^EV-[A-Z0-9][A-Z0-9_-]{1,31}$")
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=240)
    source: str = Field(min_length=1, max_length=120)
    observed_at: datetime | None = None


class DecisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    observation_metric: str = Field(min_length=1, max_length=200)
    execution_kind: str = Field(pattern=r"^(simulated_campaign|manual_recommendation)$")

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids must be unique")
        return value


class VertexDecisionPayload(BaseModel):
    """Strict schema accepted from Vertex before server-side evidence validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    priority_problem: str = Field(min_length=1, max_length=240)
    priority_reason: str = Field(min_length=1, max_length=500)
    priority_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    alternatives: list[DecisionAction] = Field(min_length=2, max_length=2)
    missing_information: list[str] = Field(default_factory=list, max_length=8)


class CompetitionAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    founder_goal: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceItem] = Field(min_length=2, max_length=30)

    @model_validator(mode="after")
    def unique_evidence(self) -> "CompetitionAnalysisRequest":
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence_id values must be unique")
        return self

    def cache_key(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class CompetitionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str
    priority_problem: str
    priority_reason: str
    priority_evidence_ids: list[str]
    alternatives: list[DecisionAction] = Field(min_length=2, max_length=2)
    missing_information: list[str]
    evidence: list[EvidenceItem]
    source: DecisionSource
    model_id: str | None
    cached: bool = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approval_status: str = "pending"
    action_digest: str
    execution_result: dict[str, str] | None = None
    failure_message: str | None = None


class CompetitionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    outcome: str = Field(pattern=r"^(approve|reject)$")
    action_digest: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=500)
