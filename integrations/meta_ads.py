"""Read-only Meta Ads campaign and creative health adapter."""

import asyncio
import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

from integrations.base import IntegrationAPIError, request_json
from integrations.metrics import (
    ads_efficiency_score,
    campaign_performance,
    decimal_value,
    diagnose_ads_health,
    percent_change,
    ratio,
    rounded,
)
from integrations.models import AdsSnapshot, CampaignPerformance, CreativePerformance, DateRange


class MetaAdsIntegrationConfig(BaseModel):
    """Meta Graph API reporting configuration."""

    model_config = ConfigDict(extra="forbid")

    access_token: SecretStr
    ad_account_id: str
    api_version: str = Field(pattern=r"^v\d+\.\d+$")
    api_base_url: HttpUrl = "https://graph.facebook.com"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    target_roas: Decimal = Field(default=Decimal("3.0"), gt=0)
    target_cac: Decimal | None = Field(default=None, gt=0)
    target_conversion_rate_pct: Decimal = Field(default=Decimal("3.0"), gt=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    # First match wins per ad row: purchases, then lead / sign-up events typical for SaaS.
    conversion_action_types: tuple[str, ...] = (
        "offsite_conversion.fb_pixel_purchase",
        "omni_purchase",
        "purchase",
        "offsite_conversion.fb_pixel_lead",
        "lead",
        "onsite_conversion.lead_grouped",
        "offsite_conversion.fb_pixel_complete_registration",
        "complete_registration",
    )

    @field_validator("ad_account_id")
    @classmethod
    def normalize_ad_account_id(cls, value: str) -> str:
        normalized = value.removeprefix("act_").strip()
        if not normalized.isdigit():
            raise ValueError("ad account ID must contain digits only")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class MetaAdsIntegration:
    """Collect ad-level rows and roll them up into campaign health."""

    provider = "meta_ads"

    def __init__(
        self,
        config: MetaAdsIntegrationConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_seconds)

    async def __aenter__(self) -> "MetaAdsIntegration":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def collect(self, period: DateRange) -> AdsSnapshot:
        current_rows, previous_rows = await asyncio.gather(
            self._insights(period), self._insights(period.previous_period())
        )
        campaigns, creatives = self._roll_up(current_rows)
        previous_campaigns, _ = self._roll_up(previous_rows)
        return self._snapshot(period, campaigns, creatives, previous_campaigns)

    async def _insights(self, period: DateRange) -> list[dict[str, Any]]:
        url = (
            f"{str(self.config.api_base_url).rstrip('/')}/{self.config.api_version}"
            f"/act_{self.config.ad_account_id}/insights"
        )
        # Send the token as a header so it never appears in URLs, logs or paging links.
        headers = {"Authorization": f"Bearer {self.config.access_token.get_secret_value()}"}
        params: dict[str, Any] = {
            "level": "ad",
            "fields": (
                "campaign_id,campaign_name,ad_id,ad_name,spend,clicks,actions,action_values"
            ),
            "time_range": json.dumps(
                {"since": period.start.isoformat(), "until": period.end.isoformat()}
            ),
            "limit": 500,
        }
        rows: list[dict[str, Any]] = []

        for _ in range(100):
            payload = await request_json(
                self._client, self.provider, "GET", url, params=params, headers=headers
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise IntegrationAPIError(
                    self.provider, None, "API returned an invalid list payload"
                )
            rows.extend(row for row in payload["data"] if isinstance(row, dict))
            after = payload.get("paging", {}).get("cursors", {}).get("after")
            if not after or not payload.get("paging", {}).get("next"):
                return rows
            params["after"] = after

        raise IntegrationAPIError(self.provider, None, "pagination exceeded the safety limit")

    def _roll_up(
        self, rows: list[dict[str, Any]]
    ) -> tuple[list[CampaignPerformance], list[CreativePerformance]]:
        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "campaign_name": "Unnamed campaign",
                "spend": Decimal("0"),
                "clicks": 0,
                "conversions": Decimal("0"),
                "conversion_value": Decimal("0"),
            }
        )
        creatives: list[CreativePerformance] = []

        for row in rows:
            campaign_id = str(row.get("campaign_id", ""))
            spend = decimal_value(row.get("spend"))
            clicks = int(decimal_value(row.get("clicks")))
            conversions, conversion_value = self._conversion_metrics(
                row.get("actions", []), row.get("action_values", [])
            )
            bucket = buckets[campaign_id]
            bucket["campaign_name"] = str(row.get("campaign_name", "Unnamed campaign"))
            bucket["spend"] += spend
            bucket["clicks"] += clicks
            bucket["conversions"] += conversions
            bucket["conversion_value"] += conversion_value
            creatives.append(
                CreativePerformance(
                    creative_id=str(row.get("ad_id", "")),
                    creative_name=str(row.get("ad_name", "Unnamed creative")),
                    campaign_id=campaign_id,
                    spend=spend,
                    clicks=clicks,
                    conversions=conversions,
                )
            )

        campaigns = [
            campaign_performance(
                campaign_id=campaign_id,
                campaign_name=values["campaign_name"],
                status=None,
                spend=values["spend"],
                clicks=values["clicks"],
                conversions=values["conversions"],
                conversion_value=values["conversion_value"],
            )
            for campaign_id, values in buckets.items()
        ]
        return campaigns, creatives

    def _conversion_metrics(
        self, actions: object, action_values: object
    ) -> tuple[Decimal, Decimal]:
        """Select one conversion event and use only its matching monetary value."""

        if not isinstance(actions, list):
            return Decimal("0"), Decimal("0")
        by_type = {
            item.get("action_type"): decimal_value(item.get("value"))
            for item in actions
            if isinstance(item, dict)
        }
        for action_type in self.config.conversion_action_types:
            if action_type in by_type:
                values = (
                    {
                        item.get("action_type"): decimal_value(item.get("value"))
                        for item in action_values
                        if isinstance(item, dict)
                    }
                    if isinstance(action_values, list)
                    else {}
                )
                return by_type[action_type], values.get(action_type, Decimal("0"))
        return Decimal("0"), Decimal("0")

    def _snapshot(
        self,
        period: DateRange,
        campaigns: list[CampaignPerformance],
        creatives: list[CreativePerformance],
        previous_campaigns: list[CampaignPerformance],
    ) -> AdsSnapshot:
        spend = sum((item.spend for item in campaigns), Decimal("0"))
        clicks = sum(item.clicks for item in campaigns)
        conversions = sum((item.conversions for item in campaigns), Decimal("0"))
        conversion_value = sum((item.conversion_value for item in campaigns), Decimal("0"))
        previous_spend = sum((item.spend for item in previous_campaigns), Decimal("0"))
        previous_conversions = sum((item.conversions for item in previous_campaigns), Decimal("0"))
        previous_value = sum((item.conversion_value for item in previous_campaigns), Decimal("0"))
        roas_value = ratio(conversion_value, spend)
        previous_roas = ratio(previous_value, previous_spend)
        roas = rounded(roas_value) if roas_value is not None else None
        cac_value = ratio(spend, conversions)
        previous_cac = ratio(previous_spend, previous_conversions)
        cac = rounded(cac_value) if cac_value is not None else None
        cac_trend = (
            percent_change(cac_value, previous_cac)
            if cac_value is not None and previous_cac is not None
            else None
        )
        trend = (
            percent_change(roas_value, previous_roas)
            if roas_value is not None and previous_roas is not None
            else None
        )

        return AdsSnapshot(
            source="meta_ads",
            period=period,
            currency=self.config.currency,
            campaigns=campaigns,
            creatives=creatives,
            spend=spend,
            clicks=clicks,
            conversions=conversions,
            conversion_value=conversion_value,
            roas=roas,
            cac=cac,
            trend_pct=trend,
            cac_trend_pct=cac_trend,
            efficiency_score=ads_efficiency_score(
                roas=roas_value,
                clicks=clicks,
                conversions=conversions,
                target_roas=self.config.target_roas,
                target_conversion_rate_pct=self.config.target_conversion_rate_pct,
            ),
            problems=diagnose_ads_health(
                spend=spend,
                conversions=conversions,
                roas=roas_value,
                cac=cac_value,
                trend_pct=trend,
                target_roas=self.config.target_roas,
                target_cac=self.config.target_cac,
            ),
        )
