"""Offline coverage for project-based Google Ads access after the token sunset.

All identities, OAuth tokens, account IDs and provider replies are synthetic fixtures.
Only metadata/reporting endpoints are allowed by these mocked transports.
"""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

import backend.api.app as api_app
import backend.api.security as security
import backend.services.real_intelligence as real_services
from backend.config import Settings
from backend.models.connectors import ConnectorProvider, GoogleAdsConnectionRequest
from backend.services.growth_intelligence import validate_live_configuration
from backend.services.phase2_runtime import build_demo_runtime
from backend.services.real_intelligence import build_real_intelligence_runtime
from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.models import DateRange

AsyncClient = httpx.AsyncClient
ACCESS = "fixture-access-token-only"
CLIENT = "fixture-client-id-only"
CLIENT_SECRET = "fixture-client-secret-only"
REFRESH = "fixture-refresh-token-only"
LEGACY_DEVELOPER = "fixture-legacy-developer-token-only"
SECRETS = (ACCESS, CLIENT_SECRET, REFRESH, LEGACY_DEVELOPER)


def auth_payload(kind):
    if kind == "access":
        return {"access_token": ACCESS}
    return {"client_id": CLIENT, "client_secret": CLIENT_SECRET, "refresh_token": REFRESH}


@pytest.mark.parametrize("token", [{}, {"developer_token": None}, {"developer_token": " \t "}])
@pytest.mark.parametrize("kind", ["access", "oauth"])
def test_connection_request_accepts_absent_or_blank_developer_token(token, kind):
    request = GoogleAdsConnectionRequest(customer_id="123-456-7890", **auth_payload(kind), **token)
    assert request.developer_token is None
    assert request.customer_id == "1234567890"


def test_legacy_developer_token_remains_accepted_and_redacted():
    request = GoogleAdsConnectionRequest(
        customer_id="1234567890", access_token=ACCESS, developer_token=LEGACY_DEVELOPER
    )
    assert request.developer_token.get_secret_value() == LEGACY_DEVELOPER
    assert all(secret not in request.model_dump_json() for secret in SECRETS)


@pytest.mark.parametrize(
    "credentials",
    [{}, {"developer_token": LEGACY_DEVELOPER}, {"client_id": CLIENT}, {"refresh_token": REFRESH}],
)
def test_optional_developer_token_does_not_replace_required_oauth(credentials):
    with pytest.raises(ValidationError):
        GoogleAdsConnectionRequest(customer_id="1234567890", **credentials)


@pytest.mark.parametrize("kind", ["access", "oauth"])
def test_settings_and_live_preflight_do_not_require_developer_token(kind):
    google = {f"google_ads_{name}": value for name, value in auth_payload(kind).items()}
    selected = Settings(
        _env_file=None,
        stripe_secret_key="rk_test_fixture_only",
        google_ads_developer_token=None,
        google_ads_customer_id="1234567890",
        meta_ads_access_token="fixture-meta-token-only",
        meta_ads_ad_account_id="9876543210",
        meta_ads_api_version="v26.0",
        **google,
    )
    assert selected.google_ads_has_credentials
    assert selected.google_ads_api_version == "v26"
    validate_live_configuration(selected)
    missing_auth = selected.model_copy(update={
        "google_ads_access_token": None,
        "google_ads_client_id": None,
        "google_ads_client_secret": None,
        "google_ads_refresh_token": None,
        "google_ads_developer_token": SecretStr(LEGACY_DEVELOPER),
    })
    assert not missing_auth.google_ads_has_credentials
    with pytest.raises(ValueError, match="GOOGLE_ADS_ACCESS_TOKEN or GOOGLE_ADS_REFRESH_TOKEN"):
        validate_live_configuration(missing_auth)


class ReadOnlyGoogleFixture:
    def __init__(self):
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        assert "developer-token" not in request.headers
        assert request.method == "POST"
        if request.url.host == "oauth2.googleapis.com":
            assert request.url.path == "/token"
            assert parse_qs(request.content.decode()) == {
                "client_id": [CLIENT],
                "client_secret": [CLIENT_SECRET],
                "refresh_token": [REFRESH],
                "grant_type": ["refresh_token"],
            }
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
        assert request.url.host == "googleads.googleapis.com"
        assert request.headers["Authorization"] == f"Bearer {ACCESS}"
        assert request.headers["login-customer-id"] == "9988776655"
        assert request.url.path.startswith("/v26/customers/1234567890/")
        query = json.loads(request.content)["query"]
        if "FROM customer" in query:
            assert request.url.path.endswith("/googleAds:search")
            return httpx.Response(200, json={"results": [{"customer": {
                "id": "1234567890", "currencyCode": "USD", "timeZone": "UTC",
                "testAccount": True,
            }}]})
        assert request.url.path.endswith("/googleAds:searchStream")
        assert "FROM campaign" in query
        return httpx.Response(200, json=[{"results": []}])


@pytest.mark.parametrize("kind", ["access", "oauth"])
@pytest.mark.parametrize("token", [None, LEGACY_DEVELOPER])
async def test_v26_reports_and_refresh_use_no_developer_header(kind, token):
    credentials = auth_payload(kind)
    if kind == "oauth":
        credentials = {f"oauth_{name}": value for name, value in credentials.items()}
    config = GoogleAdsIntegrationConfig(
        customer_id="1234567890", login_customer_id="9988776655",
        developer_token=token, **credentials,
    )
    assert config.api_version == "v26"
    fixture = ReadOnlyGoogleFixture()
    async with AsyncClient(transport=httpx.MockTransport(fixture.handle)) as client:
        integration = GoogleAdsIntegration(config, client=client)
        report = await integration.collect(DateRange(start="2026-10-01", end="2026-10-17"))
    assert report.reporting_timezone == "UTC"
    assert report.spend == 0
    assert integration.account_environment == "test"
    oauth_requests = [r for r in fixture.requests if r.url.host == "oauth2.googleapis.com"]
    assert len(oauth_requests) == (1 if kind == "oauth" else 0)
    assert len(fixture.requests) == 3 + len(oauth_requests)
    assert all("mutate" not in request.url.path for request in fixture.requests)


@pytest.fixture
def hosted_google(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, firebase_auth_required=True, firebase_project_id="offline-token-sunset",
        public_demo_only=True, live_self_service_enabled=True,
        connection_vault_key=Fernet.generate_key().decode(),
        connection_vault_path=tmp_path / "token-sunset-vault.enc",
        google_ads_access_token="operator-access-must-never-be-used",
        google_ads_developer_token="operator-developer-must-never-be-used",
        google_ads_customer_id="7777777777",
    )
    local = settings.model_copy(update={
        "firebase_auth_required": False, "live_self_service_enabled": False,
        "live_storage_backend": "local",
    })
    runtime = build_real_intelligence_runtime(local)
    runtime.connections.settings = settings
    runtime.connections.resolver.settings = settings

    def verify(token, project_id):
        assert project_id == "offline-token-sunset"
        assert token in {"judge-a", "judge-b"}
        return {"sub": token, "email_verified": True,
                "firebase": {"sign_in_provider": "google.com"}}

    monkeypatch.setattr(security, "_verify_firebase_token", verify)
    fixture = ReadOnlyGoogleFixture()
    monkeypatch.setattr(
        real_services.httpx, "AsyncClient",
        lambda **kwargs: AsyncClient(transport=httpx.MockTransport(fixture.handle), **kwargs),
    )
    app = api_app.create_app(
        runtime=build_demo_runtime(), real_intelligence=runtime, settings=settings
    )
    return SimpleNamespace(app=app, runtime=runtime, provider=fixture)


@pytest.mark.parametrize("kind", ["access", "oauth"])
@pytest.mark.parametrize("token", [{}, {"developer_token": " \t "}])
async def test_hosted_save_and_read_only_check_without_token_are_private(
    hosted_google, kind, token
):
    owner = security.live_workspace_id_for_uid("judge-a")
    headers = {"Authorization": "Bearer judge-a", "X-Tenant-ID": "local-demo"}
    foreign_headers = {"Authorization": "Bearer judge-b", "X-Tenant-ID": owner}
    body = {
        "customer_id": "123-456-7890", "login_customer_id": "998-877-6655",
        "currency": "USD", **auth_payload(kind), **token,
    }
    async with AsyncClient(
        transport=httpx.ASGITransport(app=hosted_google.app), base_url="https://offline.test"
    ) as api:
        saved = await api.put("/api/v1/connections/google_ads/config", headers=headers, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["configured"] is True
        assert saved.json()["selected_account_id"] == "1234567890"
        checked = await api.post("/api/v1/connections/google_ads/test", headers=headers)
        count = len(hosted_google.provider.requests)
        foreign = await api.get("/api/v1/connections", headers=foreign_headers)
        foreign_check = await api.post(
            "/api/v1/connections/google_ads/test", headers=foreign_headers
        )
    assert checked.status_code == 200, checked.text
    assert checked.json()["configured"] and checked.json()["checked_live"]
    assert checked.json()["success"] and checked.json()["account_environment"] == "test"
    assert count == (2 if kind == "oauth" else 1)
    assert foreign.status_code == 200 and foreign_check.status_code == 200
    assert all(not row["configured"] for row in foreign.json()["connections"])
    assert not foreign_check.json()["configured"]
    assert not foreign_check.json()["checked_live"]
    assert not foreign_check.json()["success"]
    assert len(hosted_google.provider.requests) == count
    selected = await hosted_google.runtime.connections.growth_settings(owner)
    assert selected.google_ads_has_credentials
    assert selected.google_ads_developer_token is None
    assert selected.google_ads_customer_id == "1234567890"
    assert await hosted_google.runtime.connections.vault.get(
        security.live_workspace_id_for_uid("judge-b"), ConnectorProvider.GOOGLE_ADS
    ) is None
    for response in (saved, checked, foreign, foreign_check):
        assert response.headers["Cache-Control"] == "private, no-store"
        assert all(secret not in response.text for secret in SECRETS)
        assert "operator-" not in response.text
