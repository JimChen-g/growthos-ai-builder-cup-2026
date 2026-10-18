"""Offline connection lifecycle, metadata validation and account-environment regressions."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

import backend.services.real_intelligence as real_services
from backend.config import Settings
from backend.models.connectors import ConnectionState, ConnectorProvider
from backend.services.connector_security import CredentialMetadata
from backend.services.real_intelligence import ConnectionCenterService, ConnectorCredentialResolver
from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.models import DateRange
from integrations.stripe import StripeIntegration, StripeIntegrationConfig

AsyncClient = httpx.AsyncClient
OWNER = "verified-owner-workspace"
BUSINESS = (ConnectorProvider.STRIPE, ConnectorProvider.GOOGLE_ADS, ConnectorProvider.META_ADS)


def stored_credentials(provider, *, stripe_key="rk_test_fixture_only"):
    payload = {
        ConnectorProvider.STRIPE: {"secret": stripe_key, "currency": "USD"},
        ConnectorProvider.GOOGLE_ADS: {
            "developer_token": "fixture_google_developer",
            "access_token": "fixture_google_access",
            "customer_id": "1234567890",
            "currency": "USD",
        },
        ConnectorProvider.META_ADS: {
            "access_token": "fixture_meta_access",
            "ad_account_id": "987654321",
            "api_version": "v26.0",
            "currency": "USD",
        },
    }[provider]
    return SecretStr(json.dumps(payload))


class RacingVault:
    """A successful secret read followed by a revoked lifecycle read, without a network."""

    enabled = True

    def __init__(self, provider, *, lifecycle="saved", stripe_key="rk_test_fixture_only"):
        self.provider = provider
        self.lifecycle = lifecycle
        self.records = {(OWNER, provider): stored_credentials(provider, stripe_key=stripe_key)}
        self.events = []
        self.saved_at = datetime.now(UTC) - (
            timedelta(days=8) if lifecycle == "expired" else timedelta(hours=1)
        )
        self.expires_at = self.saved_at + timedelta(days=7)

    async def get(self, tenant_id, provider):
        self.events.append(("get", tenant_id, provider))
        return self.records.get((tenant_id, provider))

    async def metadata(self, tenant_id, provider):
        self.events.append(("metadata", tenant_id, provider))
        if tenant_id != OWNER or provider is not self.provider:
            return CredentialMetadata("missing")
        if self.lifecycle != "saved":
            assert ("get", tenant_id, provider) in self.events
            self.records.pop((tenant_id, provider), None)
        return CredentialMetadata(
            self.lifecycle,
            saved_at=self.saved_at if self.lifecycle in {"saved", "expired"} else None,
            expires_at=self.expires_at if self.lifecycle in {"saved", "expired"} else None,
        )


def service_for(vault):
    settings = Settings(_env_file=None, firebase_auth_required=True)
    return ConnectionCenterService(settings, vault, ConnectorCredentialResolver(settings, vault))


def prevent_network(monkeypatch):
    def unexpected(**_kwargs):
        raise AssertionError("Connection center must not call provider APIs")

    monkeypatch.setattr(real_services.httpx, "AsyncClient", unexpected)


def mocked_http(monkeypatch, handler):
    monkeypatch.setattr(
        real_services.httpx,
        "AsyncClient",
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", BUSINESS)
@pytest.mark.parametrize("lifecycle", ["missing", "expired", "unavailable"])
async def test_center_revokes_ready_account_after_lifecycle_race(monkeypatch, provider, lifecycle):
    prevent_network(monkeypatch)
    vault = RacingVault(provider, lifecycle=lifecycle)
    service = service_for(vault)
    # The initial resolver can read an apparently configured account.
    assert await vault.get(OWNER, provider) is not None
    center = await service.center(OWNER)
    connection = next(item for item in center.connections if item.provider is provider)
    assert connection.storage_status == lifecycle
    assert not connection.configured
    assert connection.state is ConnectionState.NEEDS_CONFIGURATION
    assert connection.configuration_source is None
    assert connection.selected_account_id is None
    assert connection.currency is None
    assert connection.accounts == []
    assert connection.account_environment == "unknown"
    assert vault.events.index(("get", OWNER, provider)) < vault.events.index(
        ("metadata", OWNER, provider)
    )
    assert all(event[1] == OWNER for event in vault.events)
    assert "fixture_" not in center.model_dump_json()
    checked = await service.business_connection_test(OWNER, provider)
    assert not checked.configured and not checked.checked_live and not checked.success
    if lifecycle == "expired":
        assert connection.saved_at == vault.saved_at
        assert connection.expires_at == vault.expires_at
    else:
        assert connection.saved_at is None and connection.expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", BUSINESS)
async def test_saved_metadata_preserves_only_owners_configured_account(monkeypatch, provider):
    prevent_network(monkeypatch)
    service = service_for(RacingVault(provider))
    own = await service.center(OWNER)
    connection = next(item for item in own.connections if item.provider is provider)
    assert connection.configured and connection.storage_status == "saved"
    assert connection.state is ConnectionState.CONNECTED
    assert connection.configuration_source == "encrypted_vault"
    assert connection.currency == "USD"
    if provider is not ConnectorProvider.STRIPE:
        assert connection.selected_account_id and len(connection.accounts) == 1
    foreign = await service.center("different-owner-workspace")
    assert not any(item.configured for item in foreign.connections)
    assert all(
        item.selected_account_id is None and not item.accounts for item in foreign.connections
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [ConnectorProvider.GOOGLE_ADS, ConnectorProvider.META_ADS])
@pytest.mark.parametrize("zone", ["America/Los_Angeles", None, "Invalid/private-account-zone"])
async def test_connection_check_verifies_reporting_timezone_not_just_currency(
    monkeypatch, provider, zone
):
    calls = []

    def handler(request):
        calls.append(request)
        if provider is ConnectorProvider.GOOGLE_ADS:
            assert request.method == "POST"
            assert request.url.path.endswith("/customers/1234567890/googleAds:search")
            query = json.loads(request.content)["query"]
            assert "customer.time_zone" in query and "customer.test_account" in query
            assert request.headers["Authorization"] == "Bearer fixture_google_access"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "customer": {
                                "currencyCode": "USD",
                                "timeZone": zone,
                                "testAccount": False,
                            }
                        }
                    ]
                },
            )
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer fixture_meta_access"
        if request.url.path.endswith("/insights"):
            assert dict(request.url.params) == {
                "fields": "spend",
                "date_preset": "yesterday",
                "limit": "1",
            }
            return httpx.Response(200, json={"data": []})
        assert request.url.path.endswith("/act_987654321")
        assert request.url.params["fields"] == "currency,timezone_name"
        return httpx.Response(200, json={"currency": "USD", "timezone_name": zone})

    mocked_http(monkeypatch, handler)
    service = service_for(RacingVault(provider))
    result = await service.business_connection_test(OWNER, provider)
    assert result.configured and result.checked_live
    assert result.success is (zone == "America/Los_Angeles")
    assert len(calls) == (1 if provider is ConnectorProvider.GOOGLE_ADS else 2)
    if not result.success:
        assert "reporting timezone" in result.message
        assert result.account_environment == "unknown"
    assert "private-account-zone" not in result.model_dump_json()
    assert "fixture_" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,expected",
    [
        ("rk_test_fixture_only", "test"),
        ("rk_live_fixture_only", "live"),
        ("rk_unrecognized_fixture_only", "unknown"),
    ],
)
async def test_stripe_connection_and_snapshot_label_actual_key_environment(
    monkeypatch, key, expected
):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["Authorization"] == f"Bearer {key}"
        assert request.url.path in {"/v1/charges", "/v1/subscriptions"}
        return httpx.Response(200, json={"data": [], "has_more": False})

    mocked_http(monkeypatch, handler)
    service = service_for(RacingVault(ConnectorProvider.STRIPE, stripe_key=key))
    connection = next(
        item
        for item in (await service.center(OWNER)).connections
        if item.provider is ConnectorProvider.STRIPE
    )
    assert connection.account_environment == expected
    result = await service.business_connection_test(OWNER, ConnectorProvider.STRIPE)
    assert result.success and result.checked_live
    assert result.account_environment == expected
    assert len(calls) == 2
    async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = StripeIntegration(StripeIntegrationConfig(api_key=key), client=client)
        report = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-31"))
    assert report.account_environment == expected
    assert (
        key
        not in connection.model_dump_json() + result.model_dump_json() + report.model_dump_json()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_account,expected",
    [
        (True, "test"),
        (False, "live"),
        (None, "unknown"),
        ("false", "unknown"),
        (0, "unknown"),
        (1, "unknown"),
    ],
)
async def test_google_account_environment_requires_explicit_boolean_metadata(
    monkeypatch, test_account, expected
):
    def handler(request):
        query = json.loads(request.content)["query"]
        assert request.method == "POST"
        if "FROM customer" in query:
            assert "customer.test_account" in query
            customer = {"currencyCode": "USD", "timeZone": "UTC"}
            if test_account is not None:
                customer["testAccount"] = test_account
            return httpx.Response(200, json={"results": [{"customer": customer}]})
        assert request.url.path.endswith("googleAds:searchStream")
        return httpx.Response(200, json=[])

    mocked_http(monkeypatch, handler)
    service = service_for(RacingVault(ConnectorProvider.GOOGLE_ADS))
    result = await service.business_connection_test(OWNER, ConnectorProvider.GOOGLE_ADS)
    assert result.success and result.checked_live
    assert result.account_environment == expected
    async with AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = GoogleAdsIntegration(
            GoogleAdsIntegrationConfig(
                access_token="fixture_google_access",
                developer_token="fixture_google_developer",
                customer_id="1234567890",
            ),
            client=client,
        )
        report = await adapter.collect(DateRange(start="2026-01-01", end="2026-01-31"))
    assert report.account_environment == expected
