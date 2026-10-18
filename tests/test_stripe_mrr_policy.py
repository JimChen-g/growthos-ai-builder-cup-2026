"""Offline checks for the explicitly bounded net_recurring_v1 MRR policy."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

import integrations.stripe as stripe_module
from integrations.base import IntegrationAPIError
from integrations.models import DateRange
from integrations.stripe import MRR_POLICY, StripeIntegration, StripeIntegrationConfig

AS_OF = datetime(2026, 10, 18, tzinfo=UTC)


def price(**updates):
    return {
        "currency": "usd",
        "product": "product_a",
        "billing_scheme": "per_unit",
        "unit_amount": 10000,
        "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"},
        **updates,
    }


def item(*, selected_price=None, **updates):
    return {"quantity": 1, "price": selected_price or price(), "discounts": [], **updates}


def subscription(*, items=None, **updates):
    return {
        "status": "active",
        "currency": "usd",
        "discounts": [],
        "customer": {"object": "customer", "discount": None},
        "items": {"data": [item()] if items is None else items, "has_more": False},
        **updates,
    }


def discount(*, source=False, **coupon):
    payload = {"duration": "forever", "percent_off": 50, "applies_to": None, **coupon}
    return {"source": {"type": "coupon", "coupon": payload}} if source else {"coupon": payload}


def result(records, currency="USD"):
    # No HTTP client or credentials are needed to evaluate the pure calculation.
    adapter = object.__new__(StripeIntegration)
    adapter.config = StripeIntegrationConfig(api_key="rk_test_offline", currency=currency)
    return adapter._mrr_result(records, as_of=AS_OF)


def test_active_and_past_due_count_but_other_states_do_not():
    records = [
        subscription(status=status)
        for status in (
            "active",
            "past_due",
            "trialing",
            "unpaid",
            "canceled",
            "paused",
            "incomplete",
            "incomplete_expired",
        )
    ]
    assert result(records) == (Decimal("200.00"), [])


def test_metered_including_tiered_metered_is_excluded_not_treated_as_a_subscription_fee():
    metered = price(
        unit_amount=90000,
        billing_scheme="tiered",
        recurring={"interval": "month", "usage_type": "metered"},
    )
    records = [subscription(items=[item(), item(selected_price=metered)])]
    assert result(records) == (Decimal("100.00"), [])
    assert result([subscription(items=[item(selected_price=metered)])]) == (Decimal("0.00"), [])


@pytest.mark.parametrize("status", ["active", "past_due"])
def test_permanent_percentage_discount_is_net_mrr_with_legacy_and_current_shape(status):
    for source in (False, True):
        record = subscription(status=status, discounts=[discount(source=source)])
        assert result([record]) == (Decimal("50.00"), [])


def test_legacy_singular_discount_is_supported_without_double_counting():
    record = subscription(discount=discount())
    del record["discounts"]
    assert result([record]) == (Decimal("50.00"), [])
    record["discounts"] = [discount()]
    assert result([record]) == (Decimal("50.00"), [])


def test_item_discounts_precede_subscription_discounts_and_fixed_amount_is_not_per_unit():
    discounted_item = item(
        quantity=2, discounts=[discount(percent_off=None, amount_off=1000, currency="usd")]
    )
    record = subscription(items=[discounted_item], discounts=[discount()])
    assert result([record]) == (Decimal("95.00"), [])  # (2 × $100 − $10) × 50%


def test_permanent_fixed_discount_applies_before_annual_normalization_and_is_capped():
    annual = item(
        selected_price=price(
            unit_amount=120000, recurring={"interval": "year", "interval_count": 1}
        )
    )
    record = subscription(
        items=[annual], discounts=[discount(percent_off=None, amount_off=12000, currency="usd")]
    )
    assert result([record]) == (Decimal("90.00"), [])
    record["discounts"] = [discount(percent_off=None, amount_off=999999, currency="usd")]
    assert result([record]) == (Decimal("0.00"), [])


def test_permanent_fixed_discount_allocates_once_across_same_interval_lines():
    record = subscription(
        items=[item(), item()],
        discounts=[discount(percent_off=None, amount_off=1000, currency="usd")],
    )
    assert result([record]) == (Decimal("190.00"), [])


def test_scoped_discount_and_stacking_respect_products_and_application_order():
    record = subscription(
        items=[item(), item(selected_price=price(product="product_b"))],
        discounts=[
            discount(applies_to={"products": ["product_a"]}),
            discount(percent_off=None, amount_off=1000, currency="usd"),
        ],
    )
    assert result([record]) == (Decimal("140.00"), [])  # $50 + $100 − $10


def test_coupon_no_longer_redeemable_does_not_remove_existing_permanent_discount():
    assert result([subscription(discounts=[discount(valid=False)])]) == (Decimal("50.00"), [])


@pytest.mark.parametrize("duration", ["once", "repeating"])
def test_temporary_discounts_are_explicitly_unsupported_instead_of_guessing_dashboard_policy(
    duration,
):
    assert result([subscription(discounts=[discount(duration=duration)])]) == (
        None,
        ["mrr_temporary_discount_unsupported"],
    )


@pytest.mark.parametrize(
    "discounts",
    [
        ["di_private_id"],
        [{"source": {"type": "coupon", "coupon": "coupon_private_id"}}],
        [{"coupon": {"duration": "forever", "percent_off": 50}}],  # Product scope not expanded.
    ],
)
def test_unexpanded_discounts_never_produce_gross_mrr_or_echo_private_identifiers(discounts):
    assert result([subscription(discounts=discounts)]) == (None, ["mrr_discount_unexpanded"])


def test_tiered_or_missing_price_amount_makes_total_unavailable_not_zero_or_partial():
    for selected_price, warning in [
        (price(billing_scheme="tiered", unit_amount=None), "mrr_tiered_unsupported"),
        (price(unit_amount=None, unit_amount_decimal=None), "mrr_price_unsupported"),
    ]:
        records = [subscription(), subscription(items=[item(selected_price=selected_price)])]
        assert result(records) == (None, [warning])


def test_incomplete_subscription_item_page_makes_total_unavailable():
    record = subscription()
    record["items"]["has_more"] = True
    assert result([record]) == (None, ["mrr_items_incomplete"])


@pytest.mark.parametrize(
    "updates",
    [
        {"transform_quantity": {"divide_by": 5, "round": "up"}},
        {"custom_unit_amount": {"minimum": 10}},
        {"unit_amount": "private-invalid-amount"},
        {"unit_amount": "NaN"},
        {"unit_amount": "Infinity"},
        {"unit_amount": -1},
        {"billing_scheme": "unknown"},
        {"recurring": {"interval": "month", "interval_count": 0}},
        {"recurring": {"interval": "month", "interval_count": "0.5"}},
        {"recurring": {"interval": "month", "usage_type": "unknown"}},
        {"recurring": {"interval": "unknown"}},
        {"currency": None},
    ],
)
def test_unsupported_or_malformed_price_is_a_safe_mrr_warning(updates):
    assert result([subscription(items=[item(selected_price=price(**updates))])]) == (
        None,
        ["mrr_price_unsupported"],
    )


def test_inclusive_tax_is_not_claimed_as_tax_exclusive_mrr():
    assert result([subscription(items=[item(selected_price=price(tax_behavior="inclusive"))])]) == (
        None,
        ["mrr_tax_inclusive_unsupported"],
    )
    assert result([subscription(default_tax_rates=[{"inclusive": True, "percentage": 20}])]) == (
        None,
        ["mrr_tax_inclusive_unsupported"],
    )


def test_zero_quantity_stays_zero_and_current_item_trial_is_excluded():
    assert result([subscription(items=[item(quantity=0)])]) == (Decimal("0.00"), [])
    assert result([subscription(items=[item(), item(current_trial={"end": 9999999999})])]) == (
        Decimal("100.00"),
        [],
    )


def test_mixed_interval_fixed_discount_is_not_guessed():
    annual = item(selected_price=price(recurring={"interval": "year", "interval_count": 1}))
    record = subscription(
        items=[item(), annual],
        discounts=[discount(percent_off=None, amount_off=1000, currency="usd")],
    )
    assert result([record]) == (None, ["mrr_discount_unsupported"])


def test_scope_and_currency_uncertainty_do_not_apply_discounts_to_wrong_money():
    record = subscription(discounts=[discount(percent_off=None, amount_off=1000, currency="eur")])
    assert result([record]) == (None, ["mrr_discount_unsupported"])
    record = subscription(
        items=[item(selected_price=price(product=None))],
        discounts=[discount(applies_to={"products": ["product_a"]})],
    )
    assert result([record]) == (None, ["mrr_discount_unexpanded"])
    record = subscription(discounts=[discount(percent_off=101)])
    assert result([record]) == (None, ["mrr_discount_unsupported"])


def test_only_selected_currency_matters_and_warning_codes_are_unique_and_deterministic():
    irrelevant = subscription(
        currency="eur",
        items=[
            item(selected_price=price(currency="eur", billing_scheme="tiered", unit_amount=None))
        ],
    )
    assert result([subscription(), irrelevant]) == (Decimal("100.00"), [])
    incomplete = subscription(items=[item(selected_price=price(billing_scheme="tiered"))])
    discounted = subscription(discounts=["di_private_id"])
    assert result([incomplete, incomplete, discounted]) == (
        None,
        ["mrr_discount_unexpanded", "mrr_tiered_unsupported"],
    )


def test_jpy_permanent_fixed_coupon_uses_jpy_charge_units():
    record = subscription(
        currency="jpy",
        items=[item(selected_price=price(currency="jpy", unit_amount=1200))],
        discounts=[discount(percent_off=None, amount_off=200, currency="jpy")],
    )
    assert result([record], currency="JPY") == (Decimal("1000.00"), [])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["complete", "tiered", "subscription_read_failed"])
async def test_collection_retains_cash_metrics_and_current_mrr_metadata_without_extra_endpoints(
    case,
):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/v1/subscriptions":
            assert request.url.params.get_list("expand[]") == [
                "data.discounts",
                "data.items.data.discounts",
                "data.customer",
            ]
            if case == "subscription_read_failed":
                return httpx.Response(
                    403, json={"error": {"message": "provider-private-diagnostics"}}
                )
            record = subscription()
            if case == "tiered":
                record["items"]["data"][0]["price"]["billing_scheme"] = "tiered"
            return httpx.Response(200, json={"data": [record], "has_more": False})
        assert request.url.path == "/v1/charges"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paid": True,
                        "currency": "usd",
                        "amount_captured": 1234,
                        "amount_refunded": 34,
                        "customer": "private_customer",
                    }
                ],
                "has_more": False,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StripeIntegration(
            StripeIntegrationConfig(api_key="rk_test_offline"), client=client
        )
        before = datetime.now(UTC)
        report = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-31"))
        after = datetime.now(UTC)
    assert report.revenue == Decimal("12.00")
    assert report.customers == 1
    assert report.growth_rate_pct == 0
    assert report.momentum_score == 50
    assert report.mrr_policy == MRR_POLICY == "net_recurring_v1"
    assert before <= report.mrr_as_of <= after
    assert report.reporting_timezone == "UTC"
    assert "private" not in report.model_dump_json()
    assert len(requests) == 3
    if case == "complete":
        assert report.mrr == Decimal("100.00")
        assert report.mrr_status == "complete"
        assert report.mrr_warnings == []
    else:
        assert report.mrr is None
        assert report.mrr_status == "incomplete"
        assert report.mrr_warnings == [
            "mrr_tiered_unsupported" if case == "tiered" else "mrr_subscriptions_unavailable"
        ]


def test_customer_discount_is_inherited_only_without_subscription_discount():
    record = subscription(customer={"object": "customer", "discount": discount()})
    assert result([record]) == (Decimal("50.00"), [])
    record["discounts"] = [discount(percent_off=10)]
    assert result([record]) == (Decimal("90.00"), [])  # Not stacked with customer-level 50%.


@pytest.mark.parametrize(
    "customer", [None, "cus_private", {"deleted": True}, {}, {"id": "cus_private"}]
)
def test_unknown_customer_discount_cannot_be_reported_as_complete_gross_mrr(customer):
    assert result([subscription(customer=customer)]) == (
        None,
        ["mrr_customer_discount_unavailable"],
    )


def test_expanded_customer_without_discount_still_supports_normal_no_discount_mrr():
    # Customer.discount is a nullable object, not an omitted-by-default includable field.
    assert result([subscription(customer={"id": "cus_fixture", "object": "customer"})]) == (
        Decimal("100.00"),
        [],
    )


async def collect_records(records, coupon_handler=None, *, subscription_status=200):
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer rk_test_offline"
        if request.url.path == "/v1/subscriptions":
            assert "data.customer" in request.url.params.get_list("expand[]")
            assert all(
                len(path.split(".")) <= 4 for path in request.url.params.get_list("expand[]")
            )
            return httpx.Response(subscription_status, json={"data": records, "has_more": False})
        if request.url.path.startswith("/v1/coupons/"):
            assert dict(request.url.params) == {"expand[]": "applies_to"}
            assert coupon_handler is not None
            return await coupon_handler(request)
        assert request.url.path == "/v1/charges"
        return httpx.Response(
            200,
            json={
                "has_more": False,
                "data": [
                    {
                        "paid": True,
                        "currency": "usd",
                        "amount_captured": 10000,
                        "amount_refunded": 0,
                        "customer": "fixture_customer",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StripeIntegration(
            StripeIntegrationConfig(api_key="rk_test_offline"), client=client
        )
        report = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-31"))
    return report, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["modern", "legacy", "customer"])
async def test_real_api_discount_shapes_are_hydrated_once_per_coupon_and_not_gross(shape):
    referenced = (
        {"source": {"type": "coupon", "coupon": "coupon_fixture"}}
        if shape != "legacy"
        else {"coupon": {"id": "coupon_fixture", "duration": "forever", "percent_off": 50}}
    )
    if shape == "customer":
        record = subscription(customer={"object": "customer", "discount": referenced})
    else:
        record = subscription(discounts=[referenced])

    async def coupon_handler(request):
        assert request.url.path == "/v1/coupons/coupon_fixture"
        return httpx.Response(
            200,
            json={
                "id": "coupon_fixture",
                "duration": "forever",
                "percent_off": 50,
                "applies_to": None,
            },
        )

    report, requests = await collect_records([record, deepcopy(record)], coupon_handler)
    assert len(requests) == 4  # Two cash reads + subscriptions + one deduplicated coupon lookup.
    assert report.revenue == Decimal("100.00")
    assert report.mrr == Decimal("100.00")  # Two $100 subscriptions, each after 50% off.
    assert report.mrr_status == "complete" and not report.mrr_warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_failed_coupon_permission_or_read_does_not_return_gross_or_leak_details(
    status, caplog
):
    async def denied(_request):
        return httpx.Response(status, json={"error": {"message": "private_coupon_diagnostic"}})

    report, _ = await collect_records(
        [subscription(discounts=[{"source": {"coupon": "private_coupon_id"}}])], denied
    )
    assert report.revenue == Decimal("100.00")
    assert report.mrr is None
    assert report.mrr_warnings == ["mrr_discount_unavailable"]
    assert "private_coupon" not in report.model_dump_json()
    assert "private_coupon" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["subscriptions", "coupon"])
async def test_invalid_api_key_is_not_downgraded_to_an_mrr_only_warning(endpoint):
    async def denied(_request):
        return httpx.Response(401, json={"error": {"message": "private_auth_error"}})

    with pytest.raises(IntegrationAPIError) as error:
        await collect_records(
            [subscription(discounts=[{"source": {"coupon": "coupon_fixture"}}])],
            denied,
            subscription_status=401 if endpoint == "subscriptions" else 200,
        )
    assert error.value.status_code == 401
    assert "private_auth_error" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"id": "coupon_fixture", "duration": "forever", "percent_off": 50},
        {"id": "unexpected_coupon", "duration": "forever", "percent_off": 50, "applies_to": None},
    ],
)
async def test_coupon_lookup_still_requires_scope_and_matching_identity(payload):
    async def incomplete(_request):
        return httpx.Response(200, json=payload)

    report, _ = await collect_records(
        [subscription(discounts=[{"source": {"coupon": "coupon_fixture"}}])], incomplete
    )
    assert report.mrr is None
    assert report.mrr_warnings == ["mrr_discount_unexpanded"]


@pytest.mark.asyncio
async def test_coupon_lookup_scope_is_applied_to_correct_product():
    async def scoped(_request):
        return httpx.Response(
            200,
            json={
                "id": "coupon_fixture",
                "duration": "forever",
                "percent_off": 50,
                "applies_to": {"products": ["product_a"]},
            },
        )

    report, _ = await collect_records(
        [
            subscription(
                items=[item(), item(selected_price=price(product="product_b"))],
                discounts=[{"source": {"coupon": "coupon_fixture"}}],
            )
        ],
        scoped,
    )
    assert report.mrr == Decimal("150.00")


@pytest.mark.asyncio
async def test_coupon_limit_fails_honestly_before_issuing_extra_reads():
    records = [
        subscription(discounts=[{"source": {"coupon": f"coupon_{index}"}}]) for index in range(21)
    ]
    report, requests = await collect_records(records)
    assert len(requests) == 3
    assert report.mrr is None
    assert report.mrr_warnings == ["mrr_discount_lookup_limit"]


@pytest.mark.asyncio
async def test_coupon_reads_are_concurrency_limited_and_cached_only_for_current_report():
    active, peak, calls = 0, 0, 0

    async def delayed(request):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.005)
            return httpx.Response(
                200,
                json={
                    "id": request.url.path.rsplit("/", 1)[-1],
                    "duration": "forever",
                    "percent_off": 50,
                    "applies_to": None,
                },
            )
        finally:
            active -= 1

    records = [
        subscription(discounts=[{"source": {"coupon": f"coupon_{index}"}}]) for index in range(20)
    ]
    report, _ = await collect_records(deepcopy(records), delayed)
    assert report.mrr == Decimal("1000.00") and report.mrr_status == "complete"
    assert calls == 20 and peak <= 4 and active == 0
    await collect_records(deepcopy(records[:1]), delayed)
    assert calls == 21  # No coupon cache is carried across reports or accounts.


@pytest.mark.asyncio
async def test_coupon_reads_have_total_deadline_and_cancel_pending_reads(monkeypatch):
    monkeypatch.setattr(stripe_module, "MRR_COUPON_TIMEOUT_SECONDS", 0.01)
    active = 0

    async def stalled(_request):
        nonlocal active
        active += 1
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    records = [
        subscription(discounts=[{"source": {"coupon": f"coupon_{index}"}}]) for index in range(8)
    ]
    report, _ = await collect_records(records, stalled)
    assert active == 0
    assert report.revenue == Decimal("100.00") and report.mrr is None
    assert report.mrr_warnings == ["mrr_discount_lookup_limit"]


@pytest.mark.asyncio
async def test_coupon_auth_failure_is_not_hidden_by_another_stalled_lookup(monkeypatch):
    monkeypatch.setattr(stripe_module, "MRR_COUPON_TIMEOUT_SECONDS", 0.05)
    active = 0

    async def mixed(request):
        nonlocal active
        if request.url.path.endswith("coupon_unauthorized"):
            await asyncio.sleep(0.005)
            return httpx.Response(401, json={})
        active += 1
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    records = [
        subscription(discounts=[{"source": {"coupon": coupon_id}}])
        for coupon_id in ("coupon_unauthorized", "coupon_stalled")
    ]
    with pytest.raises(IntegrationAPIError) as error:
        await collect_records(records, mixed)
    assert error.value.status_code == 401
    assert active == 0


def test_calculation_does_not_mutate_provider_records():
    records = [subscription(discounts=[discount()])]
    original = deepcopy(records)
    assert result(records) == (Decimal("50.00"), [])
    assert records == original
