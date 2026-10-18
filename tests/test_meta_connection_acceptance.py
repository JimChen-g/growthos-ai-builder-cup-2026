"""Offline acceptance checks for encrypted Meta setup and truthful live-test results."""

import httpx
import pytest
from cryptography.fernet import Fernet

import backend.api.app as api_app
import backend.services.real_intelligence as real_services
from backend.config import Settings
from backend.models.connectors import ConnectorProvider, GeoCitation, MetaAdsConnectionRequest
from backend.services.connector_security import TenantQuotaLimiter
from backend.services.growth_intelligence import DataSourceMode, run_growth_intelligence
from backend.services.phase2_runtime import build_demo_runtime
from backend.services.real_intelligence import GeoPromptRunner, build_real_intelligence_runtime
from integrations.base import IntegrationAPIError
from integrations.geo_providers import ProviderAnswer

AsyncClient = httpx.AsyncClient
META_TOKEN = "meta-review-test-token"


def app_settings(tmp_path, **updates):
    return Settings(
        _env_file=None,
        connection_vault_key=Fernet.generate_key().decode(),
        connection_vault_path=tmp_path / "credentials.enc",
        **updates,
    )


def test_app(settings):
    return api_app.create_app(runtime=build_demo_runtime(), settings=settings)


test_app.__test__ = False


def client(app):
    return AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mock_meta_http(monkeypatch, handler):
    monkeypatch.setattr(
        real_services.httpx,
        "AsyncClient",
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
async def test_meta_save_is_encrypted_and_precedes_server_environment(tmp_path):
    settings = app_settings(
        tmp_path,
        meta_ads_access_token="old-environment-token",
        meta_ads_ad_account_id="111",
        meta_ads_api_version="v25.0",
    )
    app = test_app(settings)
    async with client(app) as api:
        selected = await api.post(
            "/api/v1/connections/meta_ads/account", json={"account_id": "111"}
        )
        assert selected.status_code == 200
        response = await api.put(
            "/api/v1/connections/meta_ads/config",
            json={
                "access_token": META_TOKEN,
                "ad_account_id": "act_987654321",
                "api_version": "v26.0",
            },
        )
        center = await api.get("/api/v1/connections")
        other_tenant = await api.get("/api/v1/connections", headers={"X-Tenant-ID": "other"})
    assert response.status_code == 200
    assert response.json()["configuration_source"] == "encrypted_vault"
    assert response.json()["selected_account_id"] == "987654321"
    assert response.json()["api_version"] == "v26.0"
    assert response.json()["supports_live_test"] is True
    assert META_TOKEN not in response.text + center.text + other_tenant.text
    assert META_TOKEN.encode() not in settings.connection_vault_path.read_bytes()
    other_meta = next(
        item for item in other_tenant.json()["connections"] if item["provider"] == "meta_ads"
    )
    assert other_meta["configured"] is False
    # A new runtime still resolves the persisted vault entry ahead of environment settings.
    restarted = build_real_intelligence_runtime(settings)
    resolved = await restarted.connections.resolver.meta_connection("local-demo")
    assert resolved.access_token.get_secret_value() == META_TOKEN
    assert resolved.ad_account_id == "987654321"
    assert resolved.api_version == "v26.0"
    other_settings = await restarted.connections.growth_settings("other")
    assert other_settings.meta_ads_access_token is None
    assert other_settings.meta_ads_ad_account_id is None
    assert other_settings.meta_ads_api_version is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"api_version": None},
        {"api_version": "latest"},
        {"ad_account_id": "act_../other"},
        {"access_token": "short"},
    ],
)
async def test_invalid_meta_configuration_never_echoes_credentials(tmp_path, changes):
    body = {"access_token": META_TOKEN, "ad_account_id": "act_123", "api_version": "v26.0"}
    body.update(changes)
    if body["api_version"] is None:
        del body["api_version"]
    async with client(test_app(app_settings(tmp_path))) as api:
        response = await api.put("/api/v1/connections/meta_ads/config", json=body)
    assert response.status_code == 422
    assert body["access_token"] not in response.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


@pytest.mark.asyncio
async def test_meta_live_test_reads_bounded_insights_without_mutation(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if not request.url.path.endswith("/insights"):
            return httpx.Response(200, json={"currency": "USD", "timezone_name": "UTC"})
        return httpx.Response(200, json={"data": []})

    settings = app_settings(tmp_path)
    app = test_app(settings)
    mock_meta_http(monkeypatch, handler)
    async with client(app) as api:
        await api.put(
            "/api/v1/connections/meta_ads/config",
            json={"access_token": META_TOKEN, "ad_account_id": "act_123", "api_version": "v26.0"},
        )
        response = await api.post("/api/v1/connections/meta_ads/test")
    assert response.status_code == 200
    assert response.json()["checked_live"] is True
    assert response.json()["success"] is True
    assert META_TOKEN not in response.text
    assert len(requests) == 2
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/v26.0/act_123/insights"
    assert dict(request.url.params) == {"fields": "spend", "date_preset": "yesterday", "limit": "1"}
    assert request.headers["Authorization"] == f"Bearer {META_TOKEN}"
    assert META_TOKEN not in str(request.url)
    metadata = requests[1]
    assert metadata.method == "GET"
    assert metadata.url.path == "/v26.0/act_123"
    assert dict(metadata.url.params) == {"fields": "currency,timezone_name"}
    assert metadata.headers["Authorization"] == f"Bearer {META_TOKEN}"
    assert META_TOKEN not in str(metadata.url)


@pytest.mark.asyncio
async def test_meta_expired_token_is_a_safe_failed_live_test(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": 190,
                    "error_subcode": 463,
                    "message": f"provider private diagnostics: {META_TOKEN}",
                }
            },
        )

    settings = app_settings(
        tmp_path,
        meta_ads_access_token=META_TOKEN,
        meta_ads_ad_account_id="123",
        meta_ads_api_version="v26.0",
    )
    mock_meta_http(monkeypatch, handler)
    async with client(test_app(settings)) as api:
        response = await api.post("/api/v1/connections/meta_ads/test")
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["checked_live"] is True
    assert response.json()["success"] is False
    assert "expired or is invalid" in response.json()["message"]
    assert META_TOKEN not in response.text
    assert "provider private diagnostics" not in response.text


@pytest.mark.asyncio
async def test_missing_meta_version_is_incomplete_and_makes_no_request(tmp_path, monkeypatch):
    def handler(request):
        pytest.fail("incomplete Meta settings must never send a provider request")

    settings = app_settings(
        tmp_path, meta_ads_access_token=META_TOKEN, meta_ads_ad_account_id="123"
    )
    mock_meta_http(monkeypatch, handler)
    async with client(test_app(settings)) as api:
        center = await api.get("/api/v1/connections")
        tested = await api.post("/api/v1/connections/meta_ads/test")
    status = next(item for item in center.json()["connections"] if item["provider"] == "meta_ads")
    assert status["configured"] is False
    assert tested.json()["configured"] is False
    assert tested.json()["checked_live"] is False
    assert tested.json()["success"] is False
    assert "API version" in tested.json()["message"]


@pytest.mark.asyncio
async def test_growth_refresh_uses_saved_meta_connection(tmp_path, monkeypatch):
    settings = app_settings(
        tmp_path,
        stripe_secret_key="stripe-review-key",
        google_ads_customer_id="555",
        google_ads_access_token="own-google-test-token",
        google_ads_developer_token="own-google-developer-token",
        meta_ads_access_token="stale-server-token",
        meta_ads_ad_account_id="999",
        meta_ads_api_version="v25.0",
    )
    demo_report = await run_growth_intelligence(Settings(_env_file=None, data_source_mode="demo"))
    captured = []

    async def fake_growth_run(selected):
        captured.append(selected)
        return demo_report.model_copy(update={"data_source_mode": DataSourceMode.LIVE})

    monkeypatch.setattr(api_app, "run_growth_intelligence", fake_growth_run)
    async with client(test_app(settings)) as api:
        await api.put(
            "/api/v1/connections/meta_ads/config",
            json={"access_token": META_TOKEN, "ad_account_id": "act_123", "api_version": "v26.0"},
        )
        response = await api.post("/api/v1/growth-intelligence/live")
    assert response.status_code == 200
    assert len(captured) == 1
    selected = captured[0]
    assert selected.data_source_mode == "live"
    assert selected.meta_ads_access_token.get_secret_value() == META_TOKEN
    assert selected.meta_ads_ad_account_id == "123"
    assert selected.meta_ads_api_version == "v26.0"
    assert selected.stripe_secret_key == settings.stripe_secret_key
    assert selected.google_ads_customer_id == "555"
    assert META_TOKEN not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("has_citations", [False, True])
async def test_geo_connection_test_reports_citations_truthfully(has_citations):
    calls = []

    class Provider:
        async def ask(self, prompt):
            calls.append(prompt)
            citations = (
                [GeoCitation(title="Example", url="https://example.com")] if has_citations else []
            )
            return ProviderAnswer(answer="Example support software.", citations=citations)

        async def aclose(self):
            pass

    class Factory:
        async def build(self, tenant_id, provider):
            return Provider()

    runner = GeoPromptRunner(Factory(), TenantQuotaLimiter(5))
    result = await runner.connection_test("local-demo", ConnectorProvider.GEMINI)
    assert result.checked_live and result.success
    assert len(calls) == 1
    assert result.message == (
        "Live grounded response completed successfully."
        if has_citations
        else "Live response completed, but no source citations were returned."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_x_authentication_failure_is_safe_and_actionable(tmp_path, monkeypatch, status_code):
    secret = "x-review-secret-token"
    runtime = build_real_intelligence_runtime(app_settings(tmp_path, x_bearer_token=secret))

    async def reject(self, brand):
        raise IntegrationAPIError("x", status_code, "API request was rejected", detail=secret)

    monkeypatch.setattr(real_services.XRecentSearch, "collect", reject)
    result = await runtime.feedback.connection_test("local-demo", ConnectorProvider.X)
    assert result.configured and result.checked_live and not result.success
    assert (
        result.message
        == "X rejected the bearer token. Replace it with a valid token and test again."
    )
    assert secret not in result.model_dump_json()


def test_meta_request_masks_access_token():
    request = MetaAdsConnectionRequest(
        access_token=META_TOKEN, ad_account_id="act_123", api_version="v26.0"
    )
    assert request.ad_account_id == "123"
    assert META_TOKEN not in request.model_dump_json()
