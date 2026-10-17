"""Offline regressions for tenant reporting currencies and actual monetary units."""

import json
from decimal import Decimal

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from backend.config import Settings
from backend.models.connectors import (
    ConnectorCredentialRequest,
    ConnectorProvider,
    GoogleAdsConnectionRequest,
    MetaAdsConnectionRequest,
)
from backend.services.real_intelligence import build_real_intelligence_runtime
from integrations.base import IntegrationError
from integrations.currency import AccountCurrencyMismatchError
from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.meta_ads import MetaAdsIntegration, MetaAdsIntegrationConfig
from integrations.models import DateRange
from integrations.stripe import StripeIntegration, StripeIntegrationConfig


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        connection_vault_key=Fernet.generate_key().decode(),
        connection_vault_path=tmp_path / "currency-vault.enc",
        stripe_currency="EUR",
        google_ads_currency="GBP",
        meta_ads_currency="CAD",
    )


def _google(currency="USD"):
    return GoogleAdsConnectionRequest(
        developer_token="own-developer-token",
        customer_id="123-456-7890",
        access_token="own-access-token",
        currency=currency,
    )


@pytest.mark.asyncio
async def test_currency_persists_with_each_tenant_connection_and_never_uses_operator_value(
    tmp_path,
):
    settings = _settings(tmp_path)
    service = build_real_intelligence_runtime(settings).connections
    await service.save_secret(
        "a", ConnectorProvider.STRIPE, SecretStr("rk_test_private"), currency="jpy"
    )
    await service.save_google_ads_connection("a", _google("HKD"))
    await service.save_meta_connection(
        "a",
        MetaAdsConnectionRequest(
            access_token="own-meta-token", ad_account_id="123", api_version="v26.0", currency="TWD"
        ),
    )
    restarted = build_real_intelligence_runtime(settings).connections
    selected = await restarted.growth_settings("a")
    assert selected.stripe_currency == "JPY"
    assert selected.google_ads_currency == "HKD"
    assert selected.meta_ads_currency == "TWD"
    assert selected.stripe_secret_key.get_secret_value() == "rk_test_private"
    statuses = {item.provider: item for item in (await restarted.center("a")).connections}
    assert statuses[ConnectorProvider.STRIPE].currency == "JPY"
    assert statuses[ConnectorProvider.GOOGLE_ADS].currency == "HKD"
    assert statuses[ConnectorProvider.META_ADS].currency == "TWD"
    assert "rk_test_private" not in (await restarted.center("a")).model_dump_json()
    other = await restarted.growth_settings("b")
    assert (other.stripe_currency, other.google_ads_currency, other.meta_ads_currency) == (
        "USD",
    ) * 3
    assert other.stripe_secret_key is None
    assert all(item.currency is None for item in (await restarted.center("b")).connections)
    # A legacy client rotating a key does not silently reset the selected currency.
    await restarted.save_secret("a", ConnectorProvider.STRIPE, SecretStr("rk_test_replacement"))
    assert (await restarted.growth_settings("a")).stripe_currency == "JPY"
    await restarted.disconnect("a", ConnectorProvider.STRIPE)
    assert (await restarted.growth_settings("a")).stripe_secret_key is None
    assert (await restarted.growth_settings("a")).stripe_currency == "USD"


@pytest.mark.asyncio
async def test_legacy_stripe_key_remains_readable_with_hosted_usd_not_operator_currency(tmp_path):
    runtime = build_real_intelligence_runtime(_settings(tmp_path))
    await runtime.connections.vault.put(
        "legacy", ConnectorProvider.STRIPE, SecretStr("rk_test_legacy")
    )
    selected = await runtime.connections.growth_settings("legacy")
    assert selected.stripe_secret_key.get_secret_value() == "rk_test_legacy"
    assert selected.stripe_currency == "USD"


@pytest.mark.parametrize("currency", ["US", "USDD", "12$", "美元", "ＵＳＤ", ""])
def test_currency_models_reject_malformed_codes(currency):
    with pytest.raises(ValidationError):
        _google(currency)
    with pytest.raises(ValidationError):
        ConnectorCredentialRequest(secret="rk_test_key", currency=currency)
    with pytest.raises(ValidationError):
        MetaAdsConnectionRequest(
            access_token="meta-token", ad_account_id="1", api_version="v26.0", currency=currency
        )


@pytest.mark.parametrize("currency", ["ZZZ", "BHD", "KWD"])
def test_stripe_rejects_unsupported_minor_unit_currencies(currency):
    with pytest.raises(ValidationError):
        StripeIntegrationConfig(api_key="rk_test_key", currency=currency)
    with pytest.raises(ValidationError):
        ConnectorCredentialRequest(secret="rk_test_key", currency=currency)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "currency,expected",
    [("JPY", "1200"), ("KRW", "1200"), ("USD", "12"), ("TWD", "12"), ("ISK", "12"), ("UGX", "12")],
)
async def test_stripe_revenue_and_mrr_use_currency_minor_units_and_filter_other_currencies(
    currency, expected
):
    def handler(request):
        if request.url.path.endswith("subscriptions"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "status": "active",
                            "items": {
                                "data": [
                                    {
                                        "quantity": 1,
                                        "price": {
                                            "currency": currency.lower(),
                                            "unit_amount": 1200,
                                            "recurring": {"interval": "month"},
                                        },
                                    },
                                    {
                                        "quantity": 1,
                                        "price": {
                                            "currency": "eur",
                                            "unit_amount": 90000,
                                            "recurring": {"interval": "month"},
                                        },
                                    },
                                ]
                            },
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paid": True,
                        "currency": currency.lower(),
                        "amount_captured": 1500,
                        "amount_refunded": 300,
                        "customer": "own-customer",
                    },
                    {
                        "paid": True,
                        "currency": "eur",
                        "amount": 90000,
                        "customer": "other-currency-customer",
                    },
                ],
                "has_more": False,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StripeIntegration(
            StripeIntegrationConfig(api_key="rk_test_key", currency=currency), client=client
        )
        snapshot = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-31"))
    assert snapshot.currency == currency
    assert snapshot.revenue == Decimal(expected)
    assert snapshot.mrr == Decimal(expected)
    assert snapshot.customers == 1


def _ads_adapter(provider, client, currency="HKD"):
    if provider == "google":
        return GoogleAdsIntegration(
            GoogleAdsIntegrationConfig(
                access_token="own-token",
                developer_token="own-developer",
                customer_id="123",
                currency=currency,
            ),
            client=client,
        )
    return MetaAdsIntegration(
        MetaAdsIntegrationConfig(
            access_token="own-token", ad_account_id="123", api_version="v26.0", currency=currency
        ),
        client=client,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "meta"])
@pytest.mark.parametrize("actual", ["USD", None, "bad-provider-private-value"])
async def test_ads_cannot_publish_mislabeled_or_unverified_metrics(provider, actual):
    calls = []

    def handler(request):
        calls.append(request)
        if provider == "google":
            assert "FROM customer" in json.loads(request.content)["query"]
            return httpx.Response(200, json={"results": [{"customer": {"currencyCode": actual}}]})
        assert request.url.params["fields"] == "currency"
        return httpx.Response(200, json={"currency": actual})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(IntegrationError) as raised:
            await _ads_adapter(provider, client).collect(
                DateRange(start="2026-01-01", end="2026-01-31")
            )
    assert len(calls) == 1  # No reporting rows read after a currency failure.
    if actual == "USD":
        assert isinstance(raised.value, AccountCurrencyMismatchError)
    assert "bad-provider-private-value" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "meta"])
async def test_ads_empty_accounts_keep_verified_non_usd_currency(provider):
    def handler(request):
        if provider == "google":
            if "FROM customer" in json.loads(request.content)["query"]:
                return httpx.Response(
                    200, json={"results": [{"customer": {"currencyCode": "HKD"}}]}
                )
            return httpx.Response(200, json=[{"results": []}])
        if request.url.path.endswith("insights"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"currency": "HKD"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _ads_adapter(provider, client).collect(
            DateRange(start="2026-01-01", end="2026-01-31")
        )
    assert result.currency == "HKD"
    assert result.spend == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [ConnectorProvider.GOOGLE_ADS, ConnectorProvider.META_ADS])
async def test_connection_test_reports_actionable_currency_mismatch(
    tmp_path, monkeypatch, provider
):
    service = build_real_intelligence_runtime(_settings(tmp_path)).connections
    if provider is ConnectorProvider.GOOGLE_ADS:
        await service.save_google_ads_connection("a", _google("HKD"))
    else:
        await service.save_meta_connection(
            "a",
            MetaAdsConnectionRequest(
                access_token="own-meta-token",
                ad_account_id="123",
                api_version="v26.0",
                currency="HKD",
            ),
        )
    original_client = httpx.AsyncClient

    def handler(request):
        if provider is ConnectorProvider.GOOGLE_ADS:
            return httpx.Response(200, json={"results": [{"customer": {"currencyCode": "USD"}}]})
        return httpx.Response(
            200, json={"data": []} if request.url.path.endswith("insights") else {"currency": "USD"}
        )

    monkeypatch.setattr(
        "backend.services.real_intelligence.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )
    result = await service.business_connection_test("a", provider)
    assert result.checked_live and not result.success
    assert "Update the connection currency" in result.message
