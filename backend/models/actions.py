"""Typed action contracts shared by agents, approvals, and integrations."""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class ActionKind(StrEnum):
    """External mutations supported in Phase 2."""

    CREATE_GOOGLE_CAMPAIGN = "create_google_campaign"
    UPDATE_GOOGLE_BUDGET = "update_google_budget"


class ExpectedKPI(BaseModel):
    """Expected campaign outcome shown to the approver."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    roas: Decimal = Field(gt=0)
    cac: Decimal | None = Field(default=None, gt=0)
    conversions: int | None = Field(default=None, ge=0)


class CampaignDraft(BaseModel):
    """Reviewable Google Search campaign draft; it has no side effects."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    campaign_name: str = Field(min_length=1, max_length=128)
    audience: str = Field(min_length=1, max_length=500)
    keywords: list[str] = Field(min_length=1, max_length=20)
    # Google responsive search ads need 3-15 headlines (<=30 chars) and 2-4 descriptions
    # (<=90 chars); validating here keeps the approved draft executable as shown.
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    final_url: AnyHttpUrl
    total_budget: Decimal = Field(gt=0, decimal_places=2)
    duration_days: int = Field(default=30, ge=1, le=365)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    expected_kpi: ExpectedKPI

    @model_validator(mode="after")
    def normalize_and_validate_assets(self) -> "CampaignDraft":
        if len(set(keyword.casefold() for keyword in self.keywords)) != len(self.keywords):
            raise ValueError("keywords must be unique")
        if any(len(headline) > 30 for headline in self.headlines):
            raise ValueError("headlines must be at most 30 characters")
        if any(len(description) > 90 for description in self.descriptions):
            raise ValueError("descriptions must be at most 90 characters")
        object.__setattr__(self, "currency", self.currency.upper())
        return self

    @property
    def daily_budget(self) -> Decimal:
        """Return the Google Ads daily budget derived from the approved total."""

        return (self.total_budget / self.duration_days).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


class ProposedAction(BaseModel):
    """Canonical action snapshot whose digest is bound to an approval decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ActionKind
    summary: str = Field(min_length=1, max_length=300)
    budget: Decimal = Field(ge=0, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    expected_kpi: ExpectedKPI
    payload: dict[str, Any]

    @classmethod
    def from_campaign_draft(cls, draft: CampaignDraft) -> "ProposedAction":
        return cls(
            kind=ActionKind.CREATE_GOOGLE_CAMPAIGN,
            summary=f"Create paused Google Search campaign: {draft.campaign_name}",
            budget=draft.total_budget,
            currency=draft.currency,
            expected_kpi=draft.expected_kpi,
            payload=draft.model_dump(mode="json"),
        )

    def digest(self) -> str:
        """Hash all mutation-relevant fields to prevent stale approval reuse."""

        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
