"""Google Ads reporting plus an explicitly invoked, approval-gated write primitive."""

import asyncio
import time
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from integrations.base import IntegrationAPIError, request_json
from integrations.currency import normalize_currency_code, verify_account_currency
from integrations.metrics import (
    ads_efficiency_score,
    campaign_performance,
    decimal_value,
    diagnose_ads_health,
    percent_change,
    ratio,
    rounded,
)
from integrations.models import (
    AdsSnapshot,
    CampaignPerformance,
    DateRange,
    verified_reporting_timezone,
)

# Google Ads limits for responsive search ads.
RSA_HEADLINE_MAX = 30
RSA_DESCRIPTION_MAX = 90
_MUTATE_RESULT_KEYS = ("campaignBudgetResult", "campaignResult", "adGroupResult", "adGroupAdResult")
_SAFE_GOOGLE_ERROR_KEYS = frozenset(
    {
        "authenticationError",
        "authorizationError",
        "customerError",
        "quotaError",
        "requestError",
    }
)


class GoogleAdsIntegrationConfig(BaseModel):
    """Google Ads OAuth and reporting configuration.

    Provide either a short-lived ``access_token`` or the OAuth client ID, client secret and
    refresh token. With a refresh token the adapter obtains and renews access tokens itself.
    """

    model_config = ConfigDict(extra="forbid")

    access_token: SecretStr | None = None
    # Backward-compatible input only; no longer transmitted after the 2026-09-09 sunset.
    developer_token: SecretStr | None = None
    customer_id: str
    login_customer_id: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: SecretStr | None = None
    oauth_refresh_token: SecretStr | None = None
    oauth_token_url: HttpUrl = "https://oauth2.googleapis.com/token"
    api_version: str = Field(default="v26", pattern=r"^v\d+$")
    api_base_url: HttpUrl = "https://googleads.googleapis.com"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    target_roas: Decimal = Field(default=Decimal("3.0"), gt=0)
    target_cac: Decimal | None = Field(default=None, gt=0)
    target_conversion_rate_pct: Decimal = Field(default=Decimal("3.0"), gt=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    contains_eu_political_advertising: bool = False

    @field_validator("customer_id", "login_customer_id")
    @classmethod
    def normalize_customer_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("-", "").strip()
        if not normalized.isdigit():
            raise ValueError("customer IDs must contain digits only")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_currency_code(value)

    @model_validator(mode="after")
    def require_credentials(self) -> "GoogleAdsIntegrationConfig":
        refresh = (self.oauth_client_id, self.oauth_client_secret, self.oauth_refresh_token)
        if any(refresh) and not all(refresh):
            raise ValueError(
                "OAuth refresh needs the client ID, client secret and refresh token together"
            )
        if self.access_token is None and not all(refresh):
            raise ValueError("an access token or an OAuth refresh token is required")
        return self

    @property
    def uses_refresh_token(self) -> bool:
        return self.oauth_refresh_token is not None


class GoogleAdsCampaignCreateRequest(BaseModel):
    """Provider request produced only from an approved campaign draft.

    When ad assets are supplied the mutation also creates the ad group, phrase-match
    keywords and one paused responsive search ad, so what the founder approved is what
    Google Ads receives.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    campaign_name: str = Field(min_length=1, max_length=128)
    daily_budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    headlines: list[str] = Field(default_factory=list, max_length=15)
    descriptions: list[str] = Field(default_factory=list, max_length=4)
    final_url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_ad_assets(self) -> "GoogleAdsCampaignCreateRequest":
        if not (self.keywords or self.headlines or self.descriptions or self.final_url):
            return self
        if not self.keywords or self.final_url is None:
            raise ValueError("an ad group needs keywords and a final URL")
        if len(self.headlines) < 3 or len(self.descriptions) < 2:
            raise ValueError("a responsive search ad needs 3+ headlines and 2+ descriptions")
        if any(len(item) > RSA_HEADLINE_MAX for item in self.headlines):
            raise ValueError(f"headlines must be at most {RSA_HEADLINE_MAX} characters")
        if any(len(item) > RSA_DESCRIPTION_MAX for item in self.descriptions):
            raise ValueError(f"descriptions must be at most {RSA_DESCRIPTION_MAX} characters")
        return self

    @property
    def has_ad_assets(self) -> bool:
        return bool(self.keywords)


class GoogleAdsCampaignCreateResult(BaseModel):
    """Auditable result of an atomic budget, campaign and ad mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_resource_name: str
    budget_resource_name: str
    ad_group_resource_name: str | None = None
    ad_resource_name: str | None = None
    keyword_count: int = 0
    status: str = "PAUSED"


class GoogleAdsIntegration:
    """Collect campaign, spend, click, conversion, ROAS, CAC, and trend metrics."""

    provider = "google_ads"

    def __init__(
        self,
        config: GoogleAdsIntegrationConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_seconds)
        self._cached_token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._account_metadata: dict[str, Any] | None = None
        self._metadata_lock = asyncio.Lock()
        self._reporting_timezone: str | None = None

    async def __aenter__(self) -> "GoogleAdsIntegration":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def collect(self, period: DateRange) -> AdsSnapshot:
        await self.verify_reporting_metadata()
        current_rows, previous_rows = await asyncio.gather(
            self._search(period), self._search(period.previous_period())
        )
        campaigns = self._campaigns(current_rows)
        previous_campaigns = self._campaigns(previous_rows)
        return self._snapshot(period, campaigns, previous_campaigns)

    async def verify_currency(self) -> str:
        """Read the immutable account currency before labeling any monetary metrics."""
        customer = await self._read_account_metadata()
        return verify_account_currency(customer.get("currencyCode"), self.config.currency)

    async def verify_reporting_metadata(self) -> str:
        """Return the verified account timezone, sharing one metadata read with currency."""
        await self.verify_currency()
        customer = await self._read_account_metadata()
        self._reporting_timezone = verified_reporting_timezone(customer.get("timeZone"))
        return self._reporting_timezone

    @property
    def account_environment(self) -> str:
        value = (self._account_metadata or {}).get("testAccount")
        return "test" if value is True else "live" if value is False else "unknown"

    async def _read_account_metadata(self) -> dict[str, Any]:
        # Adapters are scoped to one report/check, never shared between workspaces.
        async with self._metadata_lock:
            if self._account_metadata is None:
                self._account_metadata = await self._fetch_account_metadata()
            return self._account_metadata

    async def _fetch_account_metadata(self) -> dict[str, Any]:
        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            f"{str(self.config.api_base_url).rstrip('/')}/{self.config.api_version}"
            f"/customers/{self.config.customer_id}/googleAds:search",
            headers=await self._headers(),
            operation="account_metadata",
            safe_error_code_keys=_SAFE_GOOGLE_ERROR_KEYS,
            json={
                "query": (
                    "SELECT customer.id, customer.currency_code, customer.time_zone, "
                    "customer.test_account "
                    "FROM customer LIMIT 1"
                )
            },
        )
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise IntegrationAPIError(self.provider, None, "API returned invalid account metadata")
        customer = rows[0].get("customer")
        if not isinstance(customer, dict):
            raise IntegrationAPIError(self.provider, None, "API returned invalid account metadata")
        return customer

    async def create_campaign(
        self, request: GoogleAdsCampaignCreateRequest
    ) -> GoogleAdsCampaignCreateResult:
        """Atomically create a PAUSED Search campaign, its budget and approved ad assets.

        Approval is deliberately enforced by ``CampaignExecutionService``. Keeping this
        provider method free of implicit approval state makes the security boundary easy
        to test: application code must never call it directly.
        """

        if request.currency.upper() != self.config.currency.upper():
            raise ValueError(
                f"campaign currency {request.currency} does not match account currency "
                f"{self.config.currency}"
            )
        url = (
            f"{str(self.config.api_base_url).rstrip('/')}/{self.config.api_version}"
            f"/customers/{self.config.customer_id}/googleAds:mutate"
        )
        headers = await self._headers()
        customer = f"customers/{self.config.customer_id}"
        temporary_budget = f"{customer}/campaignBudgets/-1"
        temporary_campaign = f"{customer}/campaigns/-2"
        temporary_ad_group = f"{customer}/adGroups/-3"
        amount_micros = int(request.daily_budget * Decimal("1000000"))
        operations: list[dict[str, Any]] = [
            {
                "campaignBudgetOperation": {
                    "create": {
                        "resourceName": temporary_budget,
                        "name": f"{request.campaign_name} budget",
                        "amountMicros": str(amount_micros),
                        "deliveryMethod": "STANDARD",
                        "explicitlyShared": False,
                    }
                }
            },
            {
                "campaignOperation": {
                    "create": {
                        "resourceName": temporary_campaign,
                        "name": request.campaign_name,
                        "status": "PAUSED",
                        "advertisingChannelType": "SEARCH",
                        "campaignBudget": temporary_budget,
                        "manualCpc": {},
                        # Required for every new campaign under the EU political ads rules.
                        "containsEuPoliticalAdvertising": (
                            "CONTAINS_EU_POLITICAL_ADVERTISING"
                            if self.config.contains_eu_political_advertising
                            else "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
                        ),
                        "networkSettings": {
                            "targetGoogleSearch": True,
                            "targetSearchNetwork": False,
                            "targetContentNetwork": False,
                            "targetPartnerSearchNetwork": False,
                        },
                    }
                }
            },
        ]
        if request.has_ad_assets:
            operations.append(
                {
                    "adGroupOperation": {
                        "create": {
                            "resourceName": temporary_ad_group,
                            "name": f"{request.campaign_name} ad group"[:255],
                            "campaign": temporary_campaign,
                            "status": "ENABLED",
                            "type": "SEARCH_STANDARD",
                        }
                    }
                }
            )
            operations.extend(
                {
                    "adGroupCriterionOperation": {
                        "create": {
                            "adGroup": temporary_ad_group,
                            "status": "ENABLED",
                            "keyword": {"text": keyword, "matchType": "PHRASE"},
                        }
                    }
                }
                for keyword in request.keywords
            )
            operations.append(
                {
                    "adGroupAdOperation": {
                        "create": {
                            "adGroup": temporary_ad_group,
                            "status": "PAUSED",
                            "ad": {
                                "finalUrls": [str(request.final_url)],
                                "responsiveSearchAd": {
                                    "headlines": [{"text": text} for text in request.headlines],
                                    "descriptions": [
                                        {"text": text} for text in request.descriptions
                                    ],
                                },
                            },
                        }
                    }
                }
            )
        payload = {
            "mutateOperations": operations,
            "partialFailure": False,
            "responseContentType": "MUTABLE_RESOURCE",
        }
        response = await request_json(
            self._client,
            self.provider,
            "POST",
            url,
            headers=headers,
            operation="campaign_mutation",
            safe_error_code_keys=_SAFE_GOOGLE_ERROR_KEYS,
            json=payload,
        )
        if not isinstance(response, dict) or not isinstance(
            response.get("mutateOperationResponses"), list
        ):
            raise IntegrationAPIError(
                self.provider, None, "campaign mutation returned an invalid payload"
            )
        resources: dict[str, str] = {}
        keyword_count = 0
        for item in response["mutateOperationResponses"]:
            if not isinstance(item, dict):
                continue
            for key in _MUTATE_RESULT_KEYS:
                name = (item.get(key) or {}).get("resourceName")
                if name:
                    resources[key] = name
            if (item.get("adGroupCriterionResult") or {}).get("resourceName"):
                keyword_count += 1
        if "campaignBudgetResult" not in resources or "campaignResult" not in resources:
            raise IntegrationAPIError(
                self.provider, None, "campaign mutation did not return both resources"
            )
        if request.has_ad_assets and (
            "adGroupResult" not in resources or "adGroupAdResult" not in resources
        ):
            raise IntegrationAPIError(
                self.provider, None, "campaign mutation did not return the ad group and ad"
            )
        return GoogleAdsCampaignCreateResult(
            campaign_resource_name=resources["campaignResult"],
            budget_resource_name=resources["campaignBudgetResult"],
            ad_group_resource_name=resources.get("adGroupResult"),
            ad_resource_name=resources.get("adGroupAdResult"),
            keyword_count=keyword_count,
        )

    async def _search(self, period: DateRange) -> list[dict[str, Any]]:
        query = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              metrics.cost_micros,
              metrics.clicks,
              metrics.conversions,
              metrics.conversions_value
            FROM campaign
            WHERE segments.date BETWEEN '{period.start.isoformat()}' AND '{period.end.isoformat()}'
        """.strip()
        url = (
            f"{str(self.config.api_base_url).rstrip('/')}/{self.config.api_version}"
            f"/customers/{self.config.customer_id}/googleAds:searchStream"
        )
        headers = await self._headers()

        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            url,
            headers=headers,
            operation="reporting",
            safe_error_code_keys=_SAFE_GOOGLE_ERROR_KEYS,
            json={"query": query},
        )
        if not isinstance(payload, list):
            raise IntegrationAPIError(
                self.provider, None, "SearchStream returned an invalid payload"
            )

        rows: list[dict[str, Any]] = []
        for batch in payload:
            if isinstance(batch, dict) and isinstance(batch.get("results", []), list):
                rows.extend(row for row in batch.get("results", []) if isinstance(row, dict))
        return rows

    async def _access_token(self) -> str:
        if not self.config.uses_refresh_token:
            assert self.config.access_token is not None
            return self.config.access_token.get_secret_value()
        async with self._token_lock:
            if self._cached_token and time.monotonic() < self._token_expires_at:
                return self._cached_token
            assert self.config.oauth_client_secret and self.config.oauth_refresh_token
            payload = await request_json(
                self._client,
                self.provider,
                "POST",
                str(self.config.oauth_token_url),
                operation="oauth_refresh",
                data={
                    "client_id": self.config.oauth_client_id,
                    "client_secret": self.config.oauth_client_secret.get_secret_value(),
                    "refresh_token": self.config.oauth_refresh_token.get_secret_value(),
                    "grant_type": "refresh_token",
                },
            )
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not token:
                raise IntegrationAPIError(self.provider, None, "OAuth refresh returned no token")
            expires_in = float(payload.get("expires_in") or 3600)
            self._cached_token = str(token)
            # Renew a minute early so a long request never starts with an expiring token.
            self._token_expires_at = time.monotonic() + max(60.0, expires_in - 60.0)
            return self._cached_token

    async def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {await self._access_token()}",
            "Content-Type": "application/json",
        }
        if self.config.login_customer_id:
            headers["login-customer-id"] = self.config.login_customer_id
        return headers

    @staticmethod
    def _campaigns(rows: Iterable[dict[str, Any]]) -> list[CampaignPerformance]:
        campaigns: list[CampaignPerformance] = []
        for row in rows:
            campaign = row.get("campaign", {})
            metrics = row.get("metrics", {})
            spend = decimal_value(metrics.get("costMicros")) / Decimal("1000000")
            conversions = decimal_value(metrics.get("conversions"))
            conversion_value = decimal_value(metrics.get("conversionsValue"))
            campaigns.append(
                campaign_performance(
                    campaign_id=str(campaign.get("id", "")),
                    campaign_name=str(campaign.get("name", "Unnamed campaign")),
                    status=campaign.get("status"),
                    spend=spend,
                    clicks=int(decimal_value(metrics.get("clicks"))),
                    conversions=conversions,
                    conversion_value=conversion_value,
                )
            )
        return campaigns

    def _snapshot(
        self,
        period: DateRange,
        campaigns: list[CampaignPerformance],
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
            account_environment=self.account_environment,
            source="google_ads",
            period=period,
            reporting_timezone=self._reporting_timezone,
            currency=self.config.currency,
            campaigns=campaigns,
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
