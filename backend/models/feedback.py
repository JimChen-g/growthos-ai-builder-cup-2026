"""Structured Reddit/X customer-intelligence contracts for Phase 3."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FeedbackSource(StrEnum):
    REDDIT = "reddit"
    X = "x"


class FeedbackSentiment(StrEnum):
    POSITIVE = "positive"
    MIXED = "mixed"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class FeedbackSignal(BaseModel):
    """One consented or synthetic social feedback item."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source: FeedbackSource
    text: str = Field(min_length=3, max_length=1000)
    external_id: str | None = Field(default=None, min_length=1, max_length=200)


class ClassifiedFeedback(BaseModel):
    """Auditable per-message output retained behind the aggregate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: FeedbackSource
    text: str
    sentiment: FeedbackSentiment
    topics: list[str]


class CustomerProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str
    problem: str
    mentions: int = Field(ge=1)
    impact: str
    evidence: list[str] = Field(min_length=1, max_length=3)


class FeedbackAnalysis(BaseModel):
    """Founder-facing customer intelligence generated from Reddit/X signals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_count: int = Field(ge=1)
    source_counts: dict[FeedbackSource, int]
    overall_sentiment: FeedbackSentiment
    sentiment_score: Decimal = Field(ge=0, le=100)
    positive_pct: Decimal = Field(ge=0, le=100)
    negative_pct: Decimal = Field(ge=0, le=100)
    main_problem: str
    problems: list[CustomerProblem]
    recommendation: str
    analyzed_signals: list[ClassifiedFeedback]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signals: list[FeedbackSignal] = Field(min_length=1, max_length=100)
