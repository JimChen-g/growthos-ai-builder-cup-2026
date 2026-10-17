import json
from decimal import Decimal

import httpx
import pytest

from integrations.google_ads import (
    GoogleAdsCampaignCreateRequest,
    GoogleAdsIntegration,
    GoogleAdsIntegrationConfig,
)

MUTATE_OK = {
    "mutateOperationResponses": [
        {"campaignBudgetResult": {"resourceName": "customers/1234567890/campaignBudgets/1"}},
        {"campaignResult": {"resourceName": "customers/1234567890/campaigns/2"}},
        {"adGroupResult": {"resourceName": "customers/1234567890/adGroups/3"}},
        {"adGroupCriterionResult": {"resourceName": "customers/1234567890/adGroupCriteria/3~4"}},
        {"adGroupCriterionResult": {"resourceName": "customers/1234567890/adGroupCriteria/3~5"}},
        {"adGroupAdResult": {"resourceName": "customers/1234567890/adGroupAds/3~6"}},
    ]
}


def approved_request() -> GoogleAdsCampaignCreateRequest:
    return GoogleAdsCampaignCreateRequest(
        campaign_name="Acme Search",
        daily_budget=Decimal("166.67"),
        keywords=["ai customer support", "support automation software"],
        headlines=["Try Acme", "AI Customer Support", "Book a Demo Today"],
        descriptions=["Built for SaaS support teams.", "Resolve tickets faster. Start today."],
        final_url="https://acme.example.com/",
    )


@pytest.mark.asyncio
async def test_mutation_creates_every_approved_asset_and_declares_eu_status() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=MUTATE_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    integration = GoogleAdsIntegration(
        GoogleAdsIntegrationConfig(
            access_token="oauth", developer_token="developer", customer_id="123-456-7890"
        ),
        client=client,
    )
    result = await integration.create_campaign(approved_request())
    await client.aclose()

    operations = captured["mutateOperations"]
    kinds = [next(iter(operation)) for operation in operations]
    assert kinds == [
        "campaignBudgetOperation",
        "campaignOperation",
        "adGroupOperation",
        "adGroupCriterionOperation",
        "adGroupCriterionOperation",
        "adGroupAdOperation",
    ]
    campaign = operations[1]["campaignOperation"]["create"]
    assert campaign["containsEuPoliticalAdvertising"] == (
        "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    )
    assert campaign["status"] == "PAUSED"
    ad = operations[5]["adGroupAdOperation"]["create"]
    assert ad["status"] == "PAUSED"
    assert ad["ad"]["finalUrls"] == ["https://acme.example.com/"]
    assert [item["text"] for item in ad["ad"]["responsiveSearchAd"]["headlines"]] == [
        "Try Acme",
        "AI Customer Support",
        "Book a Demo Today",
    ]
    assert operations[3]["adGroupCriterionOperation"]["create"]["keyword"] == {
        "text": "ai customer support",
        "matchType": "PHRASE",
    }
    assert result.ad_group_resource_name.endswith("/3")
    assert result.ad_resource_name.endswith("/3~6")
    assert result.keyword_count == 2


def test_request_rejects_ads_google_would_refuse() -> None:
    with pytest.raises(ValueError, match="3\\+ headlines"):
        GoogleAdsCampaignCreateRequest(
            campaign_name="Acme",
            daily_budget=Decimal("10.00"),
            keywords=["acme"],
            headlines=["One"],
            descriptions=["One", "Two"],
            final_url="https://acme.example.com/",
        )


@pytest.mark.asyncio
async def test_refresh_token_is_exchanged_once_and_reused() -> None:
    token_calls: list[dict] = []
    authorizations: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            token_calls.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(200, json={"access_token": "fresh-token", "expires_in": 3599})
        authorizations.append(request.headers["Authorization"])
        return httpx.Response(200, json=MUTATE_OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    integration = GoogleAdsIntegration(
        GoogleAdsIntegrationConfig(
            developer_token="developer",
            customer_id="1234567890",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            oauth_refresh_token="refresh-token",
        ),
        client=client,
    )
    await integration.create_campaign(approved_request())
    await integration.create_campaign(approved_request())
    await client.aclose()

    assert len(token_calls) == 1
    assert token_calls[0]["grant_type"] == "refresh_token"
    assert token_calls[0]["refresh_token"] == "refresh-token"
    assert authorizations == ["Bearer fresh-token", "Bearer fresh-token"]


def test_config_requires_some_credential() -> None:
    with pytest.raises(ValueError, match="access token or an OAuth refresh token"):
        GoogleAdsIntegrationConfig(developer_token="developer", customer_id="1234567890")
