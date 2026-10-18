"""Hosted self-service credentials must never fall through to operator accounts."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from backend.config import Settings
from backend.models.connectors import (
    ConnectorProvider,
    DataMode,
    GeoScanRequest,
    GoogleAdsConnectionRequest,
    MetaAdsConnectionRequest,
    VertexAIConnectionRequest,
)
from backend.services.connector_security import (
    CredentialVaultError,
    FirestoreCredentialVault,
    FirestoreTenantQuotaLimiter,
    QuotaExceededError,
    TenantQuotaLimiter,
)
from backend.services.real_intelligence import (
    GeoPromptRunner,
    GeoProviderFactory,
    UnsupportedConnectionError,
    build_real_intelligence_runtime,
)
from integrations.geo_providers import ProviderAnswer


class Snapshot:
    def __init__(self, value):
        self.value = deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self.value)


class Ref:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def collection(self, value):
        return Ref(self.db, f"{self.path}/{value}")

    def document(self, value):
        return Ref(self.db, f"{self.path}/{value}")

    def get(self, transaction=None):
        return Snapshot(self.db.records.get(self.path))

    def set(self, value):
        self.db.records[self.path] = deepcopy(value)

    def delete(self):
        self.db.records.pop(self.path, None)


class Transaction:
    def __init__(self, db):
        self.db = db

    def set(self, ref, value):
        ref.set(value)


class Database:
    def __init__(self):
        self.records = {}
        self.lock = Lock()

    def collection(self, name):
        return Ref(self, name)

    def transaction(self):
        return Transaction(self)


def transactional(function):
    def run(transaction):
        with transaction.db.lock:
            return function(transaction)

    return run


def settings(tmp_path, **values):
    return Settings(
        _env_file=None,
        connection_vault_key=Fernet.generate_key().decode(),
        connection_vault_path=tmp_path / "vault.enc",
        **values,
    )


def google_request(**values):
    return GoogleAdsConnectionRequest(
        developer_token="judge-developer-token", customer_id="123-456-7890", **values
    )


@pytest.mark.asyncio
async def test_business_credentials_persist_and_do_not_cross_tenants(tmp_path):
    configured = settings(
        tmp_path,
        stripe_secret_key="operator-stripe",
        google_ads_access_token="operator-google",
        google_ads_developer_token="operator-developer",
        google_ads_customer_id="999",
        google_ads_login_customer_id="888",
        google_ads_client_id="operator-client",
        google_ads_client_secret="operator-secret",
        google_ads_refresh_token="operator-refresh",
        meta_ads_access_token="operator-meta",
        meta_ads_ad_account_id="777",
        meta_ads_api_version="v26.0",
    )
    first = build_real_intelligence_runtime(configured)
    await first.connections.save_secret(
        "judge-a", ConnectorProvider.STRIPE, SecretStr("rk_test_judge_key")
    )
    await first.connections.save_google_ads_connection(
        "judge-a",
        google_request(
            client_id="judge-client",
            client_secret="judge-client-secret",
            refresh_token="judge-refresh",
        ),
    )
    await first.connections.save_meta_connection(
        "judge-a",
        MetaAdsConnectionRequest(
            access_token="judge-meta-token", ad_account_id="act_123", api_version="v26.0"
        ),
    )

    # A new instance sees the same encrypted saved account selection.
    second = build_real_intelligence_runtime(configured)
    own = await second.connections.growth_settings("judge-a")
    assert own.stripe_secret_key.get_secret_value() == "rk_test_judge_key"
    assert own.google_ads_customer_id == "1234567890"
    assert own.google_ads_refresh_token.get_secret_value() == "judge-refresh"
    assert own.google_ads_access_token is None
    assert own.google_ads_login_customer_id is None
    assert own.meta_ads_ad_account_id == "123"
    other = await second.connections.growth_settings("judge-b")
    fields = (
        "stripe_secret_key",
        "google_ads_access_token",
        "google_ads_developer_token",
        "google_ads_customer_id",
        "google_ads_login_customer_id",
        "google_ads_client_id",
        "google_ads_client_secret",
        "google_ads_refresh_token",
        "meta_ads_access_token",
        "meta_ads_ad_account_id",
        "meta_ads_api_version",
    )
    assert all(getattr(other, name) is None for name in fields)
    center = await second.connections.center("judge-a")
    ads = next(c for c in center.connections if c.provider is ConnectorProvider.GOOGLE_ADS)
    assert ads.selected_account_id == "1234567890"
    assert "judge-refresh" not in center.model_dump_json()

    await second.connections.disconnect("judge-a", ConnectorProvider.GOOGLE_ADS)
    assert (await first.connections.growth_settings("judge-a")).google_ads_customer_id is None


@pytest.mark.asyncio
async def test_firebase_identity_never_receives_environment_or_adc(tmp_path):
    configured = settings(
        tmp_path,
        firebase_auth_required=True,
        stripe_secret_key="operator-secret",
        vertex_ai_project_id="operator-project",
        vertex_ai_api_key="operator-api-key",
        openai_api_key="operator-openai",
    )
    runtime = build_real_intelligence_runtime(configured)
    resolver = runtime.connections.resolver
    for tenant in ("judge-a", "local-demo"):
        assert await resolver.secret(tenant, ConnectorProvider.STRIPE) == (None, None)
        assert await resolver.secret(tenant, ConnectorProvider.OPENAI) == (None, None)
        assert await resolver.vertex_connection(tenant) is None
        with pytest.raises(UnsupportedConnectionError, match="own Vertex AI API key"):
            await runtime.connections.save_vertex_connection(
                tenant, VertexAIConnectionRequest(project_id="judge-project")
            )
    # Even an old or imported configuration without a key cannot activate server ADC.
    await runtime.connections.vault.put(
        "judge-a",
        ConnectorProvider.GEMINI_VERTEX,
        SecretStr(json.dumps({"project_id": "judge-project", "location": "global"})),
    )
    assert (
        await GeoProviderFactory(configured, resolver).build(
            "judge-a", ConnectorProvider.GEMINI_VERTEX
        )
        is None
    )


@pytest.mark.asyncio
async def test_missing_business_config_does_not_call_provider(tmp_path, monkeypatch):
    runtime = build_real_intelligence_runtime(settings(tmp_path))

    def unexpected(**kwargs):
        pytest.fail("missing configuration must not send network traffic")

    monkeypatch.setattr("backend.services.real_intelligence.httpx.AsyncClient", unexpected)
    for provider in (
        ConnectorProvider.STRIPE,
        ConnectorProvider.GOOGLE_ADS,
        ConnectorProvider.META_ADS,
    ):
        result = await runtime.connections.business_connection_test("judge-a", provider)
        assert not result.configured and not result.checked_live and not result.success


@pytest.mark.asyncio
async def test_firestore_vault_encryption_context_expiry_and_disconnect(tmp_path):
    db = Database()
    key = settings(tmp_path).connection_vault_key
    first = FirestoreCredentialVault("project", "(default)", key, client=db)
    second = FirestoreCredentialVault("project", "(default)", key, client=db)
    await first.put("judge-a", ConnectorProvider.STRIPE, SecretStr("rk_secret_never_plain"))
    assert "rk_secret_never_plain" not in repr(db.records)
    assert (await second.get("judge-a", ConnectorProvider.STRIPE)).get_secret_value() == (
        "rk_secret_never_plain"
    )
    assert await second.get("judge-b", ConnectorProvider.STRIPE) is None
    original = "live_workspaces/judge-a/connections/stripe"
    db.records["live_workspaces/judge-b/connections/stripe"] = deepcopy(db.records[original])
    with pytest.raises(CredentialVaultError):
        await second.get("judge-b", ConnectorProvider.STRIPE)
    db.records[original]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert await second.get("judge-a", ConnectorProvider.STRIPE) is None
    await first.delete("judge-a", ConnectorProvider.STRIPE)
    assert original not in db.records


@pytest.mark.asyncio
async def test_connection_lifecycle_is_private_and_does_not_claim_provider_verification(tmp_path):
    configured = settings(tmp_path, firebase_auth_required=True)
    runtime = build_real_intelligence_runtime(configured)
    db = Database()
    vault = FirestoreCredentialVault(
        "project", "(default)", configured.connection_vault_key, client=db
    )
    runtime.connections.vault = vault
    runtime.connections.resolver.vault = vault
    await runtime.connections.save_secret(
        "judge-a", ConnectorProvider.STRIPE, SecretStr("rk_private_lifecycle"), currency="USD"
    )
    own = await runtime.connections.center("judge-a")
    stripe = next(c for c in own.connections if c.provider is ConnectorProvider.STRIPE)
    assert stripe.configured and stripe.storage_status == "saved"
    assert stripe.saved_at is not None and stripe.expires_at > stripe.saved_at
    assert timedelta(days=6) < stripe.expires_at - stripe.saved_at <= timedelta(days=7, seconds=1)
    assert "rk_private_lifecycle" not in own.model_dump_json()
    assert "ciphertext" not in own.model_dump_json()
    assert "checked_at" not in own.model_dump_json()  # Saving never claims a successful live test.
    other = await runtime.connections.center("judge-b")
    assert all(c.storage_status == "missing" for c in other.connections)
    assert all(c.saved_at is None and c.expires_at is None for c in other.connections)
    path = "live_workspaces/judge-a/connections/stripe"
    db.records[path]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    expired = await runtime.connections.center("judge-a")
    stripe = next(c for c in expired.connections if c.provider is ConnectorProvider.STRIPE)
    assert not stripe.configured and stripe.storage_status == "expired"
    assert stripe.saved_at is not None
    # Once TTL has physically deleted the record, the UI must not invent an expiry history.
    await vault.delete("judge-a", ConnectorProvider.STRIPE)
    assert (await vault.metadata("judge-a", ConnectorProvider.STRIPE)).storage_status == "missing"


@pytest.mark.asyncio
async def test_vault_unavailable_metadata_does_not_access_storage():
    vault = FirestoreCredentialVault("project", "(default)", None, client=Database())
    metadata = await vault.metadata("judge-a", ConnectorProvider.STRIPE)
    assert metadata.storage_status == "unavailable"
    assert metadata.saved_at is None and metadata.expires_at is None


@pytest.mark.asyncio
async def test_local_vault_lifecycle_does_not_fabricate_historical_timestamps(tmp_path):
    runtime = build_real_intelligence_runtime(settings(tmp_path))
    await runtime.connections.save_secret(
        "judge-a", ConnectorProvider.STRIPE, SecretStr("rk_local_lifecycle")
    )
    metadata = await runtime.connections.vault.metadata("judge-a", ConnectorProvider.STRIPE)
    assert metadata.storage_status == "saved"
    assert metadata.saved_at is None and metadata.expires_at is None


@pytest.mark.asyncio
async def test_firestore_quota_is_shared_across_instances_and_tenant_scoped(monkeypatch):
    monkeypatch.setattr("google.cloud.firestore.transactional", transactional)
    db = Database()
    first = FirestoreTenantQuotaLimiter("project", "(default)", 2, client=db)
    second = FirestoreTenantQuotaLimiter("project", "(default)", 2, client=db)
    await first.consume("judge-a", ConnectorProvider.STRIPE)
    await second.consume("judge-a", ConnectorProvider.STRIPE)
    with pytest.raises(QuotaExceededError):
        await first.consume("judge-a", ConnectorProvider.STRIPE)
    await second.consume("judge-b", ConnectorProvider.STRIPE)
    await second.consume("judge-a", ConnectorProvider.GOOGLE_ADS)
    assert await second.used("judge-a", ConnectorProvider.STRIPE) == 2
    assert await second.used("judge-b", ConnectorProvider.STRIPE) == 1


def test_google_connection_requires_complete_authentication():
    with pytest.raises(ValidationError):
        google_request()
    with pytest.raises(ValidationError):
        google_request(client_id="client-only", access_token="access-token")
    assert google_request(access_token="access-token").customer_id == "1234567890"
    assert google_request(
        client_id="client", client_secret="client-secret", refresh_token="refresh-token"
    )


@pytest.mark.asyncio
async def test_provider_test_errors_cannot_echo_credentials(tmp_path, monkeypatch):
    runtime = build_real_intelligence_runtime(settings(tmp_path))
    secret = "rk_test_private_value"
    await runtime.connections.save_secret("judge-a", ConnectorProvider.STRIPE, SecretStr(secret))
    original_client = httpx.AsyncClient

    def handler(request):
        return httpx.Response(403, json={"error": {"message": secret}})

    monkeypatch.setattr(
        "backend.services.real_intelligence.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )
    result = await runtime.connections.business_connection_test("judge-a", ConnectorProvider.STRIPE)
    assert result.checked_live and not result.success
    assert secret not in result.model_dump_json()
    assert "credentials and account access" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [ConnectorProvider.STRIPE, ConnectorProvider.GOOGLE_ADS])
async def test_business_connection_test_uses_only_bounded_reads(tmp_path, monkeypatch, provider):
    runtime = build_real_intelligence_runtime(settings(tmp_path))
    if provider is ConnectorProvider.STRIPE:
        await runtime.connections.save_secret("judge-a", provider, SecretStr("rk_test_own_key"))
    else:
        await runtime.connections.save_google_ads_connection(
            "judge-a", google_request(access_token="judge-own-access-token")
        )
    captured = []
    original_client = httpx.AsyncClient

    def handler(request):
        captured.append(request)
        if provider is ConnectorProvider.STRIPE:
            assert request.method == "GET"
            assert request.url.params["limit"] == "1"
            assert request.headers["authorization"] == "Bearer rk_test_own_key"
            return httpx.Response(200, json={"data": []})
        assert request.method == "POST"
        assert request.url.path.endswith("/customers/1234567890/googleAds:search")
        assert json.loads(request.content)["query"] == (
            "SELECT customer.id, customer.currency_code, customer.time_zone, "
            "customer.test_account FROM customer LIMIT 1"
        )
        assert request.headers["authorization"] == "Bearer judge-own-access-token"
        return httpx.Response(
            200, json={"results": [{"customer": {
                "id": "1234567890", "currencyCode": "USD", "timeZone": "UTC"
            }}]}
        )

    monkeypatch.setattr(
        "backend.services.real_intelligence.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )
    result = await runtime.connections.business_connection_test("judge-a", provider)
    assert result.configured and result.checked_live and result.success
    assert len(captured) == (2 if provider is ConnectorProvider.STRIPE else 1)


def test_hosted_live_requires_durable_encrypted_store(tmp_path):
    with pytest.raises(RuntimeError, match="persistent Firestore"):
        build_real_intelligence_runtime(
            settings(tmp_path, firebase_auth_required=True, live_self_service_enabled=True)
        )
    with pytest.raises(RuntimeError, match="Firebase project"):
        build_real_intelligence_runtime(settings(tmp_path, live_storage_backend="firestore"))


@pytest.mark.asyncio
async def test_geo_deadline_returns_partial_results_and_cancels_pending_calls():
    closed = []
    cancelled = []

    class Provider:
        async def ask(self, prompt):
            if prompt == "Return the successful response":
                return ProviderAnswer(answer="Acme is listed.", citations=[])
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(prompt)

        async def aclose(self):
            closed.append(True)

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    runner = GeoPromptRunner(
        Factory(), TenantQuotaLimiter(50), request_timeout_seconds=1, total_timeout_seconds=0.04
    )
    request = GeoScanRequest(
        brand="Acme",
        website="https://example.com",
        category="software",
        providers=[ConnectorProvider.PERPLEXITY],
        mode=DataMode.LIVE,
        prompts=[
            "Return the successful response",
            "This response never arrives",
            "Another slow response",
        ],
    )
    started = monotonic()
    report = await runner.scan("judge-a", request)
    assert monotonic() - started < 0.5
    assert report.providers[0].status == "partial"
    assert report.providers[0].successful_prompts == 1
    assert report.results[0].answer == "Acme is listed."
    assert all(item.error and not item.answer for item in report.results[1:])
    assert len(cancelled) == 2 and closed == [True]


@pytest.mark.asyncio
async def test_geo_concurrency_is_bounded_across_providers_and_keeps_order():
    active = 0
    maximum = 0
    provider_active = {}
    provider_peak = {}
    release = asyncio.Event()

    class Provider:
        def __init__(self, provider):
            self.provider = provider

        async def ask(self, prompt):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            provider_active[self.provider] = provider_active.get(self.provider, 0) + 1
            provider_peak[self.provider] = max(
                provider_peak.get(self.provider, 0), provider_active[self.provider]
            )
            if active == 6:
                release.set()
            try:
                await release.wait()
                return ProviderAnswer(answer=f"Acme: {prompt}", citations=[])
            finally:
                active -= 1
                provider_active[self.provider] -= 1

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider(provider)

    runner = GeoPromptRunner(Factory(), TenantQuotaLimiter(50), total_timeout_seconds=0.2)
    request = GeoScanRequest(
        brand="Acme", website="https://example.com", category="software", mode=DataMode.LIVE
    )
    report = await runner.scan("judge-a", request)
    assert maximum == 6
    assert max(provider_peak.values()) <= 3
    assert all(item.status == "completed" for item in report.providers)
    assert [item.prompt for item in report.results] == request.prompts * len(request.providers)


@pytest.mark.asyncio
async def test_geo_per_call_timeout_is_visible_and_does_not_fake_success():
    class Provider:
        async def ask(self, prompt):
            await asyncio.Event().wait()

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    runner = GeoPromptRunner(
        Factory(), TenantQuotaLimiter(50), request_timeout_seconds=0.01, total_timeout_seconds=0.2
    )
    report = await runner.scan(
        "judge-a",
        GeoScanRequest(
            brand="Acme",
            website="https://example.com",
            category="software",
            mode=DataMode.LIVE,
            providers=[ConnectorProvider.PERPLEXITY],
        ),
    )
    assert report.providers[0].status == "unavailable"
    assert all("timed out" in item.error and not item.answer for item in report.results)
