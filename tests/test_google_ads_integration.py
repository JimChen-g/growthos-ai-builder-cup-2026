import json
from decimal import Decimal

import httpx
import pytest

from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.models import DateRange


def _google_handler(request: httpx.Request) -> httpx.Response:
    query = json.loads(request.content)["query"]
    current = "2026-01-01" in query
    metrics = (
        {
            "costMicros": "100000000",
            "clicks": "100",
            "conversions": 5,
            "conversionsValue": 250,
        }
        if current
        else {
            "costMicros": "100000000",
            "clicks": "100",
            "conversions": 5,
            "conversionsValue": 300,
        }
    )
    return httpx.Response(
        200,
        json=[
            {
                "results": [
                    {
                        "campaign": {"id": "123", "name": "Search", "status": "ENABLED"},
                        "metrics": metrics,
                    }
                ]
            }
        ],
    )


@pytest.mark.asyncio
async def test_google_ads_collects_health_and_flags_decline() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_google_handler))
    integration = GoogleAdsIntegration(
        GoogleAdsIntegrationConfig(
            access_token="oauth_token",
            developer_token="developer_token",
            customer_id="123-456-7890",
            target_cac=15,
        ),
        client=client,
    )

    snapshot = await integration.collect(DateRange(start="2026-01-01", end="2026-01-31"))

    assert snapshot.spend == Decimal("100")
    assert snapshot.roas == Decimal("2.5")
    assert snapshot.cac == Decimal("20.0")
    assert snapshot.cac_trend_pct == Decimal("0.0")
    assert snapshot.trend_pct == Decimal("-16.7")
    assert snapshot.efficiency_score == Decimal("88.3")
    assert snapshot.problems == ["ROAS below target", "High CAC", "ROAS declining"]
    await client.aclose()
