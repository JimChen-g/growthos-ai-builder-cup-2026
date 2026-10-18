"""Read-only Stripe revenue adapter for the Phase 1 snapshot."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

from backend.currency import normalize_stripe_currency
from integrations.base import IntegrationAPIError, IntegrationError, request_json
from integrations.metrics import decimal_value, percent_change, revenue_momentum_score
from integrations.models import DateRange, RevenueSnapshot

CENT = Decimal("0.01")
# Stripe charge/price minor units, not payout formatting. ISK and UGX intentionally
# retain the two-decimal API representation; HUF and TWD charges do too.
# https://docs.stripe.com/currencies#zero-decimal
ZERO_DECIMAL_CURRENCIES = frozenset(
    "BIF CLP DJF GNF JPY KMF KRW MGA PYG RWF VND VUV XAF XOF XPF".split()
)
MRR_POLICY = "net_recurring_v1"


def stripe_account_environment(secret: SecretStr | None) -> str:
    """Expose only the key's documented mode, never any part of the credential."""
    if secret is None:
        return "unknown"
    value = secret.get_secret_value()
    if value.startswith(("rk_test_", "sk_test_")):
        return "test"
    if value.startswith(("rk_live_", "sk_live_")):
        return "live"
    return "unknown"
MRR_COUPON_LIMIT = 20
MRR_COUPON_CONCURRENCY = 4
MRR_COUPON_TIMEOUT_SECONDS = 8.0


class _MRRIncomplete(ValueError):
    """An internal stable warning code, never a provider payload or account identifier."""


@dataclass(slots=True)
class _RecurringAmount:
    amount: Decimal
    interval: str
    count: Decimal
    product: str | None


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
        """Collect a snapshot using bounded, read-only Stripe requests."""

        previous = period.previous_period()
        collected = await asyncio.gather(
            self._list_charges(period),
            self._list_charges(previous),
            self._list_all(
                "/v1/subscriptions",
                {
                    "status": "all",
                    "limit": 100,
                    # Customer is expandable; its nullable discount is already an object.
                    # The item-discount expansion is four levels, Stripe's maximum.
                    "expand[]": ["data.discounts", "data.items.data.discounts", "data.customer"],
                },
            ),
            return_exceptions=True,
        )
        current_charges, previous_charges, subscriptions = collected
        # A failure reading subscriptions must not turn valid cash revenue into zero.
        for charges in (current_charges, previous_charges):
            if isinstance(charges, BaseException):
                raise charges
        mrr_as_of = datetime.now(UTC)
        if isinstance(subscriptions, IntegrationAPIError) and subscriptions.status_code == 401:
            # An invalid/revoked key is not merely an unsupported MRR calculation.
            raise subscriptions
        if isinstance(subscriptions, IntegrationError):
            mrr, mrr_warnings = None, ["mrr_subscriptions_unavailable"]
        elif isinstance(subscriptions, BaseException):
            raise subscriptions
        else:
            try:
                await self._hydrate_mrr_coupons(subscriptions)
            except _MRRIncomplete as error:
                mrr, mrr_warnings = None, [str(error)]
            else:
                mrr_as_of = datetime.now(UTC)
                mrr, mrr_warnings = self._mrr_result(subscriptions, as_of=mrr_as_of)

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
            account_environment=stripe_account_environment(self.config.api_key),
            currency=self.config.currency.upper(),
            revenue=current_revenue,
            customers=len(customers),
            mrr=mrr,
            mrr_status="incomplete" if mrr_warnings else "complete",
            mrr_policy=MRR_POLICY,
            mrr_warnings=mrr_warnings,
            mrr_as_of=mrr_as_of,
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

    def _monthly_recurring_revenue(self, subscriptions: list[dict[str, Any]]) -> Decimal | None:
        return self._mrr_result(subscriptions, as_of=datetime.now(UTC))[0]

    async def _hydrate_mrr_coupons(self, subscriptions: list[dict[str, Any]]) -> None:
        """Resolve coupon scope once per ID, within a per-report request/time budget.

        Discount.source.coupon can remain an ID even when a discount is expanded.
        Coupon.applies_to is not returned by default; fetching it directly avoids
        illegal five/six-level subscription expansions. Never cache across reports.
        """
        pending: dict[str, list[dict[str, Any]]] = {}
        for subscription in subscriptions:
            if subscription.get("status") not in {"active", "past_due"}:
                continue
            currency = subscription.get("currency")
            if isinstance(currency, str) and currency.lower() != self.config.currency:
                continue
            items = subscription.get("items", {})
            rows = items.get("data") if isinstance(items, dict) else None
            if not isinstance(rows, list):
                continue  # The pure calculation supplies the item-specific warning.
            discounts: list[Any] = []
            relevant_items = False
            for item in rows:
                if not isinstance(item, dict) or not isinstance(item.get("price"), dict):
                    continue
                price = item["price"]
                recurring = price.get("recurring")
                if (
                    str(price.get("currency", "")).lower() != self.config.currency
                    or not isinstance(recurring, dict)
                    or recurring.get("usage_type") == "metered"
                    or item.get("current_trial")
                ):
                    continue
                relevant_items = True
                discounts.extend(self._discounts(item))
            if not relevant_items:
                continue
            discounts.extend(self._effective_subscription_discounts(subscription))
            for discount in discounts:
                if not isinstance(discount, dict):
                    raise _MRRIncomplete("mrr_discount_unexpanded")
                coupon = self._coupon(discount)
                if isinstance(coupon, dict) and "applies_to" in coupon:
                    continue
                coupon_id = coupon.get("id") if isinstance(coupon, dict) else coupon
                if not isinstance(coupon_id, str) or not coupon_id or len(coupon_id) > 200:
                    raise _MRRIncomplete("mrr_discount_unexpanded")
                pending.setdefault(coupon_id, []).append(discount)
                if len(pending) > MRR_COUPON_LIMIT:
                    raise _MRRIncomplete("mrr_discount_lookup_limit")
        if not pending:
            return
        semaphore = asyncio.Semaphore(MRR_COUPON_CONCURRENCY)

        async def fetch(coupon_id: str) -> dict[str, Any]:
            async with semaphore:
                payload = await request_json(
                    self._client,
                    self.provider,
                    "GET",
                    f"{str(self.config.api_base_url).rstrip('/')}/v1/coupons/"
                    f"{quote(coupon_id, safe='')}",
                    params={"expand[]": "applies_to"},
                    headers={"Authorization": f"Bearer {self.config.api_key.get_secret_value()}"},
                )
                if (
                    not isinstance(payload, dict)
                    or payload.get("id") != coupon_id
                    or "applies_to" not in payload
                ):
                    raise _MRRIncomplete("mrr_discount_unexpanded")
                return payload

        async def resolve(coupon_id: str) -> dict[str, Any] | Exception:
            try:
                return await fetch(coupon_id)
            except IntegrationAPIError as error:
                if error.status_code == 401:
                    raise
                return error
            except Exception as error:
                return error

        try:
            async with asyncio.timeout(MRR_COUPON_TIMEOUT_SECONDS):
                tasks = [asyncio.create_task(resolve(coupon_id)) for coupon_id in pending]
                try:
                    # Authentication failure is immediate, even if a peer lookup stalls.
                    results = await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            raise _MRRIncomplete("mrr_discount_lookup_limit") from None
        for result in results:
            if isinstance(result, _MRRIncomplete):
                raise result
            if isinstance(result, IntegrationError):
                raise _MRRIncomplete("mrr_discount_unavailable") from None
            if isinstance(result, BaseException):
                raise result
        for discounts, coupon in zip(pending.values(), results, strict=True):
            for discount in discounts:
                discount["coupon"] = coupon

    def _mrr_result(
        self, subscriptions: list[dict[str, Any]], *, as_of: datetime
    ) -> tuple[Decimal | None, list[str]]:
        """Current licensed net run-rate, not a historical Stripe Dashboard reproduction.

        Only permanent, fully expanded coupons are supported. Temporary discounts,
        complex prices or missing data make the whole selected-currency MRR unavailable;
        a subtotal of supported subscriptions must never masquerade as the total.
        """
        minor_units = Decimal("0")
        warnings: set[str] = set()
        for subscription in subscriptions:
            if subscription.get("status") not in {"active", "past_due"}:
                continue
            subscription_currency = subscription.get("currency")
            try:
                if subscription_currency is not None:
                    if not isinstance(subscription_currency, str):
                        raise _MRRIncomplete("mrr_price_unsupported")
                    if subscription_currency.lower() != self.config.currency:
                        continue
                amounts = self._subscription_amounts(subscription, as_of)
                minor_units += sum(
                    (self._to_monthly(line.amount, line.interval, line.count) for line in amounts),
                    Decimal("0"),
                )
            except _MRRIncomplete as error:
                warnings.add(str(error))
        return (None if warnings else self._major_units(minor_units)), sorted(warnings)

    def _subscription_amounts(
        self, subscription: dict[str, Any], as_of: datetime
    ) -> list[_RecurringAmount]:
        items = subscription.get("items")
        if (
            not isinstance(items, dict)
            or not isinstance(items.get("data"), list)
            or items.get("has_more")
        ):
            raise _MRRIncomplete("mrr_items_incomplete")
        amounts: list[_RecurringAmount] = []
        excluded_items = False
        for item in items["data"]:
            if not isinstance(item, dict) or not isinstance(item.get("price"), dict):
                raise _MRRIncomplete("mrr_price_unsupported")
            price = item["price"]
            price_currency = price.get("currency")
            if not isinstance(price_currency, str):
                raise _MRRIncomplete("mrr_price_unsupported")
            if price_currency.lower() != self.config.currency:
                if subscription.get("currency") == self.config.currency:
                    # A multi-currency Price needs its selected currency option expanded.
                    raise _MRRIncomplete("mrr_price_unsupported")
                excluded_items = True
                continue
            recurring = price.get("recurring")
            if not isinstance(recurring, dict):
                raise _MRRIncomplete("mrr_price_unsupported")
            if recurring.get("usage_type") == "metered" or item.get("current_trial"):
                excluded_items = True
                continue
            if price.get("billing_scheme") == "tiered":
                raise _MRRIncomplete("mrr_tiered_unsupported")
            if (
                price.get("billing_scheme") not in {None, "per_unit"}
                or recurring.get("usage_type") not in {None, "licensed"}
                or price.get("transform_quantity")
                or price.get("custom_unit_amount")
            ):
                raise _MRRIncomplete("mrr_price_unsupported")
            tax_rates = item.get("tax_rates") or subscription.get("default_tax_rates") or []
            if (
                not isinstance(tax_rates, list)
                or price.get("tax_behavior") == "inclusive"
                or any(not isinstance(rate, dict) or rate.get("inclusive") for rate in tax_rates)
            ):
                raise _MRRIncomplete("mrr_tax_inclusive_unsupported")
            interval = recurring.get("interval")
            count = self._nonnegative_number(recurring.get("interval_count", 1))
            if (
                interval not in {"month", "year", "week", "day"}
                or count <= 0
                or count != count.to_integral_value()
            ):
                raise _MRRIncomplete("mrr_price_unsupported")
            raw_amount = price.get("unit_amount_decimal")
            if raw_amount is None:
                raw_amount = price.get("unit_amount")
            quantity = self._nonnegative_number(item.get("quantity", 1))
            if quantity != quantity.to_integral_value():
                raise _MRRIncomplete("mrr_price_unsupported")
            product = price.get("product")
            if isinstance(product, dict):
                product = product.get("id")
            line = _RecurringAmount(
                amount=self._nonnegative_number(raw_amount) * quantity,
                interval=interval,
                count=count,
                product=product if isinstance(product, str) else None,
            )
            self._apply_discounts([line], self._discounts(item), as_of)
            amounts.append(line)
        discounts = self._effective_subscription_discounts(subscription) if amounts else []
        if discounts and excluded_items and amounts:
            # A subscription-wide fixed amount may be shared with excluded metered,
            # trial or foreign-currency lines. Never guess the allocation.
            raise _MRRIncomplete("mrr_discount_unsupported")
        if amounts:
            self._apply_discounts(amounts, discounts, as_of)
        return amounts

    @staticmethod
    def _nonnegative_number(value: Any) -> Decimal:
        try:
            if value is None or isinstance(value, bool):
                raise ValueError
            number = Decimal(str(value))
            if not number.is_finite() or number < 0:
                raise ValueError
            return number
        except (ValueError, ArithmeticError):
            raise _MRRIncomplete("mrr_price_unsupported") from None

    @staticmethod
    def _discounts(owner: dict[str, Any]) -> list[Any]:
        discounts = owner.get("discounts")
        if discounts is None:
            return [owner["discount"]] if owner.get("discount") else []
        if not isinstance(discounts, list):
            raise _MRRIncomplete("mrr_discount_unexpanded")
        return discounts

    def _effective_subscription_discounts(self, subscription: dict[str, Any]) -> list[Any]:
        discounts = self._discounts(subscription)
        if discounts:
            return discounts
        # Stripe inherits the customer discount when the subscription has none.
        # Expanded customers may omit a null discount; an ID/deleted object is unknown.
        customer = subscription.get("customer")
        if (
            not isinstance(customer, dict)
            or customer.get("deleted")
            or (
                customer.get("object") != "customer"
                and "discount" not in customer
                and "discounts" not in customer
            )
        ):
            raise _MRRIncomplete("mrr_customer_discount_unavailable")
        return self._discounts(customer)

    @staticmethod
    def _coupon(discount: dict[str, Any]) -> Any:
        source = discount.get("source") or {}
        return discount.get("coupon") or (
            source.get("coupon") if isinstance(source, dict) else None
        )

    def _apply_discounts(
        self, amounts: list[_RecurringAmount], discounts: list[Any], as_of: datetime
    ) -> None:
        for discount in discounts:
            if not isinstance(discount, dict):
                raise _MRRIncomplete("mrr_discount_unexpanded")
            coupon = self._coupon(discount)
            if not isinstance(coupon, dict) or "applies_to" not in coupon:
                # Modern coupons can omit product restrictions unless expanded. Missing
                # scope is not evidence that a coupon applies to the entire subtotal.
                raise _MRRIncomplete("mrr_discount_unexpanded")
            if coupon.get("duration") != "forever":
                raise _MRRIncomplete("mrr_temporary_discount_unsupported")
            start = discount.get("start", 0)
            if (
                not isinstance(start, (int, float))
                or discount.get("end") is not None
                or start > as_of.timestamp()
            ):
                raise _MRRIncomplete("mrr_discount_unsupported")
            scope = coupon.get("applies_to")
            eligible = amounts
            if scope is not None:
                if not isinstance(scope, dict) or not isinstance(scope.get("products"), list):
                    raise _MRRIncomplete("mrr_discount_unsupported")
                if any(line.product is None for line in amounts):
                    raise _MRRIncomplete("mrr_discount_unexpanded")
                eligible = [line for line in amounts if line.product in scope["products"]]
            if not eligible:
                continue
            if coupon.get("percent_off") is not None:
                percent = self._nonnegative_number(coupon["percent_off"])
                if percent > 100 or coupon.get("amount_off") is not None:
                    raise _MRRIncomplete("mrr_discount_unsupported")
                for line in eligible:
                    line.amount *= (Decimal("100") - percent) / Decimal("100")
            elif coupon.get("amount_off") is not None:
                if (
                    coupon.get("currency") != self.config.currency
                    or len({(line.interval, line.count) for line in eligible}) > 1
                ):
                    raise _MRRIncomplete("mrr_discount_unsupported")
                reduction = self._nonnegative_number(coupon["amount_off"])
                total = sum((line.amount for line in eligible), Decimal("0"))
                factor = max(Decimal("0"), total - reduction) / total if total else Decimal("0")
                for line in eligible:
                    line.amount *= factor
            else:
                raise _MRRIncomplete("mrr_discount_unsupported")

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
