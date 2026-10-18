from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from integrations.models import DateRange
from integrations.stripe import StripeIntegration, StripeIntegrationConfig


def _stripe_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/subscriptions":
        return httpx.Response(
            200,
            json={
                "has_more": False,
                "data": [
                    {
                        "id": "sub_monthly",
                        "status": "active",
                        "customer": {"discount": None},
                        "items": {
                            "data": [
                                {
                                    "quantity": 1,
                                    "price": {
                                        "currency": "usd",
                                        "unit_amount": 5000,
                                        "recurring": {"interval": "month", "interval_count": 1},
                                    },
                                }
                            ]
                        },
                    },
                    {
                        "id": "sub_yearly",
                        "status": "active",
                        "customer": {"discount": None},
                        "items": {
                            "data": [
                                {
                                    "quantity": 1,
                                    "price": {
                                        "currency": "usd",
                                        "unit_amount": 120000,
                                        "recurring": {"interval": "year", "interval_count": 1},
                                    },
                                }
                            ]
                        },
                    },
                ],
            },
        )

    start_timestamp = int(request.url.params["created[gte]"])
    start_month = datetime.fromtimestamp(start_timestamp, tz=UTC).month
    if start_month == 1:
        charges = [
            {
                "id": "ch_current_1",
                "paid": True,
                "currency": "usd",
                "amount_captured": 10000,
                "amount_refunded": 1000,
                "customer": "cus_1",
            },
            {
                "id": "ch_current_2",
                "paid": True,
                "currency": "usd",
                "amount_captured": 6000,
                "amount_refunded": 0,
                "customer": "cus_2",
            },
        ]
    else:
        charges = [
            {
                "id": "ch_previous",
                "paid": True,
                "currency": "usd",
                "amount_captured": 10000,
                "amount_refunded": 0,
                "customer": "cus_1",
            }
        ]
    return httpx.Response(200, json={"has_more": False, "data": charges})


@pytest.mark.asyncio
async def test_stripe_collects_revenue_customers_mrr_and_momentum() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_stripe_handler))
    integration = StripeIntegration(
        StripeIntegrationConfig(api_key="sk_test_not_a_secret"), client=client
    )

    snapshot = await integration.collect(DateRange(start="2026-01-01", end="2026-01-31"))

    assert snapshot.revenue == Decimal("150.00")
    assert snapshot.customers == 2
    assert snapshot.mrr == Decimal("150.00")
    assert snapshot.growth_rate_pct == Decimal("50.0")
    assert snapshot.momentum_score == Decimal("100.0")
    await client.aclose()
