"""Read-only Stripe revenue adapter for the Phase 1 snapshot."""

import asyncio
from datetime import UTC, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

from backend.currency import normalize_stripe_currency
from integrations.base import IntegrationAPIError, request_json
from integrations.metrics import decimal_value, percent_change, revenue_momentum_score
from integrations.models import DateRange, RevenueSnapshot

CENT = Decimal("0.01")
# Stripe charge/price minor units, not payout formatting. ISK and UGX intentionally
# retain the two-decimal API representation; HUF and TWD charges do too.
# https://docs.stripe.com/currencies#zero-decimal
ZERO_DECIMAL_CURRENCIES = frozenset(
    "BIF CLP DJF GNF JPY KMF KRW MGA PYG RWF VND VUV XAF XOF XPF".split()
)


class StripeIntegrationConfig(BaseModel):
    """Credentials and boundaries for Stripe's read-only API."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    currency: str = Field(default="usd", min_length=3, max_length=3)
    api_base_url: HttpUrl = "https://api.stripe.com"
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_stripe_currency(value).lower()


class StripeIntegration:
    """Collect revenue, paying customers, MRR, and period growth from Stripe."""

    provider = "stripe"

    def __init__(
        self,
        config: StripeIntegrationConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_seconds)

    async def __aenter__(self) -> "StripeIntegration":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def collect(self, period: DateRange) -> RevenueSnapshot:
        """Collect a normalized snapshot using only Stripe list endpoints."""

        previous = period.previous_period()
        current_charges, previous_charges, subscriptions = await asyncio.gather(
            self._list_charges(period),
            self._list_charges(previous),
            self._list_all("/v1/subscriptions", {"status": "all", "limit": 100}),
        )

        current_revenue = self._net_revenue(current_charges)
        previous_revenue = self._net_revenue(previous_charges)
        growth_rate = percent_change(current_revenue, previous_revenue)
        customers = {
            charge["customer"]
            for charge in current_charges
            if self._is_eligible_charge(charge) and charge.get("customer")
        }

        return RevenueSnapshot(
            period=period,
            currency=self.config.currency.upper(),
            revenue=current_revenue,
            customers=len(customers),
            mrr=self._monthly_recurring_revenue(subscriptions),
            growth_rate_pct=growth_rate,
            momentum_score=revenue_momentum_score(growth_rate),
        )

    async def _list_charges(self, period: DateRange) -> list[dict[str, Any]]:
        start = datetime.combine(period.start, time.min, tzinfo=UTC)
        end = datetime.combine(period.end, time.max, tzinfo=UTC)
        return await self._list_all(
            "/v1/charges",
            {
                "created[gte]": int(start.timestamp()),
                "created[lte]": int(end.timestamp()),
                "limit": 100,
            },
        )

    async def _list_all(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page_params = dict(params)
        url = f"{str(self.config.api_base_url).rstrip('/')}{path}"

        for _ in range(100):
            payload = await request_json(
                self._client,
                self.provider,
                "GET",
                url,
                params=page_params,
                headers={"Authorization": f"Bearer {self.config.api_key.get_secret_value()}"},
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise IntegrationAPIError(
                    self.provider, None, "API returned an invalid list payload"
                )

            page = payload["data"]
            records.extend(item for item in page if isinstance(item, dict))
            if not payload.get("has_more"):
                return records
            if not page or not page[-1].get("id"):
                raise IntegrationAPIError(self.provider, None, "pagination cursor is missing")
            page_params["starting_after"] = page[-1]["id"]

        raise IntegrationAPIError(self.provider, None, "pagination exceeded the safety limit")

    def _net_revenue(self, charges: list[dict[str, Any]]) -> Decimal:
        minor_units = sum(
            (
                decimal_value(charge.get("amount_captured", charge.get("amount")))
                - decimal_value(charge.get("amount_refunded"))
                for charge in charges
                if self._is_eligible_charge(charge)
            ),
            Decimal("0"),
        )
        return self._major_units(minor_units)

    def _is_eligible_charge(self, charge: dict[str, Any]) -> bool:
        return bool(
            charge.get("paid")
            and not charge.get("failure_code")
            and charge.get("currency", "").lower() == self.config.currency
        )

    def _monthly_recurring_revenue(self, subscriptions: list[dict[str, Any]]) -> Decimal:
        minor_units = Decimal("0")
        for subscription in subscriptions:
            if subscription.get("status") != "active":
                continue
            items = subscription.get("items", {}).get("data", [])
            for item in items:
                price = item.get("price", {})
                if price.get("currency", "").lower() != self.config.currency:
                    continue
                recurring = price.get("recurring") or {}
                interval = recurring.get("interval")
                interval_count = decimal_value(recurring.get("interval_count") or 1)
                if interval_count <= 0:
                    continue
                amount = decimal_value(
                    price.get("unit_amount_decimal", price.get("unit_amount"))
                ) * decimal_value(item.get("quantity") or 1)
                minor_units += self._to_monthly(amount, interval, interval_count)
        return self._major_units(minor_units)

    def _major_units(self, minor_units: Decimal) -> Decimal:
        divisor = (
            Decimal("1")
            if self.config.currency.upper() in ZERO_DECIMAL_CURRENCIES
            else Decimal("100")
        )
        return (minor_units / divisor).quantize(CENT, rounding=ROUND_HALF_UP)

    @staticmethod
    def _to_monthly(amount: Decimal, interval: str | None, count: Decimal) -> Decimal:
        if interval == "month":
            return amount / count
        if interval == "year":
            return amount / (Decimal("12") * count)
        if interval == "week":
            return amount * Decimal("52") / (Decimal("12") * count)
        if interval == "day":
            return amount * Decimal("365") / (Decimal("12") * count)
        return Decimal("0")
