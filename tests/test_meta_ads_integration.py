import json
from decimal import Decimal

import httpx
import pytest

from integrations.meta_ads import MetaAdsIntegration, MetaAdsIntegrationConfig
from integrations.models import DateRange


def _meta_row(ad_id: str, spend: str, clicks: str, conversions: str, value: str) -> dict:
    return {
        "campaign_id": "campaign_1",
        "campaign_name": "Prospecting",
        "ad_id": ad_id,
        "ad_name": f"Creative {ad_id}",
        "spend": spend,
        "clicks": clicks,
        "actions": [
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": conversions},
            {"action_type": "purchase", "value": conversions},
        ],
        "action_values": [
            {"action_type": "offsite_conversion.fb_pixel_purchase", "value": value},
            {"action_type": "purchase", "value": value},
        ],
    }


def _meta_handler(request: httpx.Request) -> httpx.Response:
    if not request.url.path.endswith("/insights"):
        return httpx.Response(200, json={"currency": "USD", "timezone_name": "UTC"})
    time_range = json.loads(request.url.params["time_range"])
    rows = (
        [_meta_row("ad_1", "60", "70", "3", "180"), _meta_row("ad_2", "40", "30", "2", "120")]
        if time_range["since"] == "2026-01-01"
        else [_meta_row("ad_previous", "100", "100", "5", "400")]
    )
    return httpx.Response(200, json={"data": rows})


@pytest.mark.asyncio
async def test_meta_ads_collects_campaign_and_creative_health() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_meta_handler))
    integration = MetaAdsIntegration(
        MetaAdsIntegrationConfig(
            access_token="meta_token",
            ad_account_id="act_123456",
            api_version="v25.0",
            target_cac=15,
        ),
        client=client,
    )

    snapshot = await integration.collect(DateRange(start="2026-01-01", end="2026-01-31"))

    assert snapshot.spend == Decimal("100")
    assert snapshot.conversions == Decimal("5")
    assert snapshot.roas == Decimal("3.0")
    assert snapshot.cac == Decimal("20.0")
    assert snapshot.cac_trend_pct == Decimal("0.0")
    assert snapshot.trend_pct == Decimal("-25.0")
    assert len(snapshot.campaigns) == 1
    assert len(snapshot.creatives) == 2
    assert snapshot.problems == ["High CAC", "ROAS declining"]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actions", "values", "expected_conversions", "expected_value"),
    [
        ([("purchase", "5"), ("lead", "20")], [("lead", "100")], "5", "0"),
        (
            [("purchase", "5"), ("lead", "20")],
            [("purchase", "250"), ("lead", "100")],
            "5",
            "250",
        ),
        ([("lead", "20")], [("lead", "100")], "20", "100"),
        ([("purchase", "5")], [], "5", "0"),
        ([("purchase", "0"), ("lead", "20")], [("lead", "100")], "0", "0"),
        ([], [("purchase", "250")], "0", "0"),
    ],
    ids=[
        "purchase-count-does-not-use-lead-value",
        "purchase-and-lead-are-not-added-together",
        "lead-fallback-uses-lead-value",
        "missing-values-stays-zero",
        "zero-purchases-do-not-switch-events",
        "value-without-conversion-event-is-not-used",
    ],
)
async def test_meta_ads_matches_conversion_value_to_selected_event(
    actions: list[tuple[str, str]],
    values: list[tuple[str, str]],
    expected_conversions: str,
    expected_value: str,
) -> None:
    row = _meta_row("ad_1", "100", "100", "0", "0")
    row["actions"] = [{"action_type": kind, "value": value} for kind, value in actions]
    row["action_values"] = [{"action_type": kind, "value": value} for kind, value in values]

    async def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/insights"):
            return httpx.Response(200, json={"currency": "USD", "timezone_name": "UTC"})
        return httpx.Response(200, json={"data": [row]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        integration = MetaAdsIntegration(
            MetaAdsIntegrationConfig(
                access_token="meta_token",
                ad_account_id="act_123456",
                api_version="v25.0",
            ),
            client=client,
        )
        snapshot = await integration.collect(DateRange(start="2026-01-01", end="2026-01-31"))

    assert snapshot.conversions == Decimal(expected_conversions)
    assert snapshot.conversion_value == Decimal(expected_value)
    assert snapshot.campaigns[0].conversion_value == Decimal(expected_value)
    assert snapshot.creatives[0].conversions == Decimal(expected_conversions)
