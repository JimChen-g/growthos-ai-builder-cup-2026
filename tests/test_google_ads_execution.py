import json
from decimal import Decimal

import httpx
import pytest

from integrations.google_ads import (
    GoogleAdsCampaignCreateRequest,
    GoogleAdsIntegration,
    GoogleAdsIntegrationConfig,
)


@pytest.mark.asyncio
async def test_google_ads_creates_paused_campaign_and_budget_atomically() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.url.path.endswith("/customers/1234567890/googleAds:mutate")
        return httpx.Response(
            200,
            json={
                "mutateOperationResponses": [
                    {
                        "campaignBudgetResult": {
                            "resourceName": "customers/1234567890/campaignBudgets/456"
                        }
                    },
                    {"campaignResult": {"resourceName": "customers/1234567890/campaigns/789"}},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    integration = GoogleAdsIntegration(
        GoogleAdsIntegrationConfig(
            access_token="oauth",
            developer_token="developer",
            customer_id="123-456-7890",
            currency="USD",
        ),
        client=client,
    )

    result = await integration.create_campaign(
        GoogleAdsCampaignCreateRequest(
            campaign_name="Acme Search",
            daily_budget=Decimal("166.67"),
            currency="USD",
        )
    )

    operations = captured["mutateOperations"]
    budget = operations[0]["campaignBudgetOperation"]["create"]
    campaign = operations[1]["campaignOperation"]["create"]
    assert captured["partialFailure"] is False
    assert budget["amountMicros"] == "166670000"
    assert campaign["status"] == "PAUSED"
    assert campaign["campaignBudget"].endswith("/-1")
    assert result.campaign_resource_name.endswith("/789")
    assert result.budget_resource_name.endswith("/456")
    await client.aclose()
