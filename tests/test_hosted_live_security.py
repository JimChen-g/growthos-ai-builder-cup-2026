"""Offline hosted acceptance: verified identities own isolated read-only Live workspaces."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

import backend.api.app as api_app
import backend.api.security as security
from backend.config import Settings
from backend.models.audit_log import AgentActionStatus
from backend.models.connectors import ConnectorProvider, DataMode
from backend.services.audit_log import AuditLogService
from backend.services.phase2_runtime import build_demo_runtime
from backend.services.real_intelligence import build_real_intelligence_runtime
from integrations.base import IntegrationAPIError, IntegrationTransportError, request_json

IDENTITIES = {
    "anonymous": {
        "sub": "anonymous-uid", "email_verified": False,
        "firebase": {"sign_in_provider": "anonymous"},
    },
    "google-a": {
        "sub": "verified-google-a", "email_verified": True,
        "firebase": {"sign_in_provider": "google.com"},
    },
    "password-unverified": {
        "sub": "unverified-password", "email_verified": False,
        "firebase": {"sign_in_provider": "password"},
    },
    "formal-b": {
        "sub": "verified-password-b", "email_verified": True,
        "firebase": {"sign_in_provider": "password"},
    },
}


def headers(identity="google-a", tenant="chosen-by-client"):
    return {"Authorization": f"Bearer {identity}", "X-Tenant-ID": tenant}


def workspace(identity):
    return security.live_workspace_id_for_uid(IDENTITIES[identity]["sub"])


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    def verify(token, project_id):
        assert project_id == "offline-hosted-test"
        if token not in IDENTITIES:
            raise ValueError("private verification diagnostics must not escape")
        return IDENTITIES[token]

    monkeypatch.setattr(security, "_verify_firebase_token", verify)
    settings = Settings(
        _env_file=None,
        firebase_auth_required=True,
        firebase_project_id="offline-hosted-test",
        public_demo_only=True,
        live_self_service_enabled=True,
        connection_vault_key=Fernet.generate_key().decode(),
        connection_vault_path=tmp_path / "credentials.enc",
        # Deliberately configured owner credentials must never reach hosted users.
        stripe_secret_key="rk_server-owner-stripe",
        google_ads_access_token="server-owner-google-access",
        google_ads_developer_token="server-owner-google-developer",
        google_ads_customer_id="111111",
        google_ads_login_customer_id="222222",
        google_ads_client_id="server-owner-google-client",
        google_ads_client_secret="server-owner-google-secret",
        google_ads_refresh_token="server-owner-google-refresh",
        meta_ads_access_token="server-owner-meta-access",
        meta_ads_ad_account_id="333333",
        meta_ads_api_version="v26.0",
        vertex_ai_project_id="server-owner-project",
        vertex_ai_api_key="server-owner-vertex-key",
        openai_api_key="server-owner-openai-key",
        gemini_api_key="server-owner-gemini-key",
        perplexity_api_key="server-owner-perplexity-key",
        x_bearer_token="server-owner-x-token",
    )
    # Explicitly inject a local vault for offline API tests. Production rejects this
    # storage combination; the resolver still applies the hosted no-fallback policy.
    local_settings = settings.model_copy(update={
        "firebase_auth_required": False, "live_self_service_enabled": False,
        "live_storage_backend": "local",
    })
    real = build_real_intelligence_runtime(local_settings)
    real.connections.settings = settings
    real.connections.resolver.settings = settings
    ledgers = {}

    def audit_factory(project_id, database_id, workspace_id, retention_days):
        assert project_id == "offline-hosted-test"
        return ledgers.setdefault(workspace_id, AuditLogService(mode=DataMode.LIVE))

    def unexpected_history(*args, **kwargs):
        raise AssertionError("This offline scenario must not persist a successful measurement")

    monkeypatch.setattr(api_app, "FirestoreAuditLogService", audit_factory)
    monkeypatch.setattr(api_app, "FirestoreIntelligenceHistoryService", unexpected_history)
    app = api_app.create_app(
        runtime=build_demo_runtime(), real_intelligence=real, settings=settings,
    )
    return SimpleNamespace(app=app, settings=settings, real=real, ledgers=ledgers)


def client(hosted):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hosted.app, client=("203.0.113.7", 12345)),
        base_url="https://offline.test",
    )


@pytest.mark.parametrize(
    ("identity", "allowed"),
    [("anonymous", False), ("password-unverified", False), ("google-a", True), ("formal-b", True)],
)
async def test_me_reports_verified_identity_and_server_owned_workspace(hosted, identity, allowed):
    async with client(hosted) as api:
        response = await api.get("/api/v1/me", headers=headers(identity, "victim-workspace"))
    assert response.status_code == 200
    assert response.json() == {
        "uid": IDENTITIES[identity]["sub"],
        "workspace_id": workspace(identity) if allowed else None,
        "authenticated": allowed,
        "live_allowed": allowed,
    }
    if allowed:
        assert workspace(identity) != security.workspace_id_for_uid(IDENTITIES[identity]["sub"])
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Authorization" in response.headers["Vary"]


@pytest.mark.parametrize("token", [None, "expired"])
async def test_hosted_requests_require_verified_firebase_tokens(hosted, token):
    async with client(hosted) as api:
        response = await api.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {token}"} if token else {},
        )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "private verification" not in response.text
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("identity", ["anonymous", "password-unverified"])
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/v1/connections"),
    ("PUT", "/api/v1/connections/stripe/credential"),
    ("DELETE", "/api/v1/connections/stripe"),
    ("POST", "/api/v1/growth-intelligence/live"),
    ("GET", "/api/v1/audit-log?mode=live"),
    ("GET", "/api/v1/intelligence-history?mode=live"),
    ("POST", "/api/v1/geo/scans"),
    ("POST", "/api/v1/feedback/collect"),
])
async def test_unregistered_or_unverified_users_cannot_touch_live(hosted, identity, method, path):
    async with client(hosted) as api:
        response = await api.request(method, path, headers=headers(identity))
    assert response.status_code == 403
    assert not hosted.settings.connection_vault_path.exists()
    assert response.headers["Cache-Control"] == "private, no-store"


async def test_live_requires_explicit_self_service_opt_in(hosted):
    hosted.settings.live_self_service_enabled = False
    async with client(hosted) as api:
        me = await api.get("/api/v1/me", headers=headers())
        denied = await api.get("/api/v1/connections", headers=headers())
    assert me.json()["authenticated"] is True
    assert me.json()["live_allowed"] is False
    assert me.json()["workspace_id"] is None
    assert denied.status_code == 403


async def test_connections_start_empty_never_inherit_owner_environment(hosted):
    async with client(hosted) as api:
        response = await api.get("/api/v1/connections", headers=headers(tenant="local-demo"))
    assert response.status_code == 200
    assert response.json()["tenant_id"] == workspace("google-a")
    assert all(not item["configured"] for item in response.json()["connections"])
    assert "server-owner" not in response.text
    selected = await hosted.real.connections.growth_settings(workspace("google-a"))
    for field in (
        "stripe_secret_key", "google_ads_access_token", "google_ads_developer_token",
        "google_ads_customer_id", "google_ads_login_customer_id", "google_ads_client_id",
        "google_ads_client_secret", "google_ads_refresh_token", "meta_ads_access_token",
        "meta_ads_ad_account_id", "meta_ads_api_version",
    ):
        assert getattr(selected, field) is None
    assert await hosted.real.connections.resolver.vertex_connection(workspace("google-a")) is None


async def test_growth_without_user_connections_reports_configuration_not_demo_data(hosted):
    async with client(hosted) as api:
        response = await api.post(
            "/api/v1/growth-intelligence/live", headers=headers(tenant="local-demo"),
        )
    assert response.status_code == 409
    assert "missing configuration" in response.json()["detail"]
    assert "server-owner" not in response.text
    assert "growth_score" not in response.json()
    assert hosted.ledgers == {}


@pytest.mark.parametrize(("path", "body"), [
    ("/api/v1/geo/scans", {
        "mode": "live", "brand": "Judge A", "website": "https://judge.example.test",
        "category": "Support", "providers": ["openai"], "prompts": ["Recommend support tools"],
    }),
    ("/api/v1/feedback/collect", {
        "mode": "live", "brand": "Judge A", "sources": ["reddit", "x"],
    }),
])
async def test_unconfigured_scans_do_not_record_successful_measurements(hosted, path, body):
    async with client(hosted) as api:
        response = await api.post(path, headers=headers(tenant="local-demo"), json=body)
    assert response.status_code == 200
    assert response.json()["tenant_id"] == workspace("google-a")
    assert response.json()["mode"] == "live"
    events = await hosted.ledgers[workspace("google-a")].all_entries()
    assert len(events) == 1
    assert events[0].status is AgentActionStatus.FAILED
    assert "server-owner" not in response.text
    # The fixture fails before any Firestore initialization if history is requested.


async def test_save_read_disconnect_ignore_client_tenant_and_isolate_other_account(hosted):
    token = "rk_judge-a-private-stripe"
    async with client(hosted) as api:
        saved = await api.put(
            "/api/v1/connections/stripe/credential", headers=headers(tenant=workspace("formal-b")),
            json={"secret": token},
        )
        own = await api.get("/api/v1/connections", headers=headers(tenant="local-demo"))
        other = await api.get(
            "/api/v1/connections", headers=headers("formal-b", workspace("google-a")),
        )
        other_delete = await api.delete(
            "/api/v1/connections/stripe", headers=headers("formal-b", workspace("google-a")),
        )
        still_own = await api.get("/api/v1/connections", headers=headers())
        removed = await api.delete("/api/v1/connections/stripe", headers=headers())
        empty = await api.get("/api/v1/connections", headers=headers())
    assert all(r.status_code == 200 for r in (
        saved, own, other, other_delete, still_own, removed, empty,
    ))
    assert saved.json()["configured"] is True
    assert next(p for p in own.json()["connections"] if p["provider"] == "stripe")["configured"]
    assert all(not p["configured"] for p in other.json()["connections"])
    own_stripe = next(p for p in still_own.json()["connections"] if p["provider"] == "stripe")
    assert own_stripe["configured"]
    assert removed.json()["configured"] is False
    assert all(not p["configured"] for p in empty.json()["connections"])
    assert token not in "".join(r.text for r in (saved, own, other, removed))
    assert token.encode() not in hosted.settings.connection_vault_path.read_bytes()
    other_secret = await hosted.real.connections.vault.get(
        workspace("formal-b"), ConnectorProvider.STRIPE,
    )
    assert other_secret is None


async def test_google_ads_config_is_owned_and_redacted(hosted):
    body = {
        "developer_token": "judge-google-developer-secret", "customer_id": "123-456-7890",
        "access_token": "judge-google-access-secret",
    }
    async with client(hosted) as api:
        response = await api.put(
            "/api/v1/connections/google_ads/config", headers=headers(), json=body,
        )
        other = await api.get("/api/v1/connections", headers=headers("formal-b"))
    assert response.status_code == 200
    assert response.json()["selected_account_id"] == "1234567890"
    assert response.json()["configured"] is True
    assert all(not p["configured"] for p in other.json()["connections"])
    assert body["access_token"] not in response.text + other.text
    assert body["developer_token"] not in response.text + other.text


async def test_hosted_vertex_requires_users_own_key_without_adc_fallback(hosted):
    async with client(hosted) as api:
        response = await api.put(
            "/api/v1/connections/gemini_vertex/config", headers=headers(),
            json={"project_id": "judge-project", "location": "global"},
        )
    assert response.status_code == 409
    assert "own Vertex AI API key" in response.text
    assert not hosted.settings.connection_vault_path.exists()


@pytest.mark.parametrize("public_demo_only", [True, False])
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/v1/workflows/shared-run"),
    ("POST", "/api/v1/workflows/shared-run/decision"),
    ("POST", "/api/v1/workflows/demo"),
    ("GET", "/api/v1/approvals"),
    ("GET", "/api/v1/audit-log?mode=demo"),
    ("GET", "/api/v1/intelligence-history?mode=demo"),
    ("POST", "/api/v1/feedback/analyze"),
])
async def test_legacy_shared_workflows_stay_closed_on_hosted_server(
    hosted, public_demo_only, method, path,
):
    hosted.settings.public_demo_only = public_demo_only
    async with client(hosted) as api:
        response = await api.request(method, path, headers=headers())
    assert response.status_code == 403


@pytest.mark.parametrize(("path", "body", "secret"), [
    ("/api/v1/connections/google_ads/config", {
        "developer_token": "never-echo-this-google-token", "customer_id": "123",
    }, "never-echo-this-google-token"),
    ("/api/v1/connections/meta_ads/config", {
        "access_token": "never-echo-this-meta-token", "ad_account_id": "bad",
        "api_version": "v26.0",
    }, "never-echo-this-meta-token"),
    ("/api/v1/connections/openai/credential", {"secret": "short"}, "short"),
])
async def test_invalid_config_is_redacted_and_not_cacheable(hosted, path, body, secret):
    async with client(hosted) as api:
        response = await api.put(path, headers=headers(), json=body)
    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in item and "ctx" not in item for item in response.json()["detail"])
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Authorization" in response.headers["Vary"]


async def test_private_audit_reads_only_verified_workspace_ignoring_tenant_header(hosted):
    ledger = AuditLogService(mode=DataMode.LIVE)
    hosted.ledgers[workspace("google-a")] = ledger
    await ledger.record(
        agent="Judge A", action="Private source checked", reason="Own account only",
        status=AgentActionStatus.EXECUTED,
    )
    async with client(hosted) as api:
        own = await api.get("/api/v1/audit-log?mode=live", headers=headers())
        other = await api.get(
            "/api/v1/audit-log?mode=live", headers=headers("formal-b", workspace("google-a")),
        )
    assert own.status_code == other.status_code == 200
    assert own.json()["total"] == 1
    assert other.json()["total"] == 0
    assert "Private source checked" not in other.text
    assert own.headers["Cache-Control"] == other.headers["Cache-Control"] == "private, no-store"


async def test_hosted_live_deadline_returns_safe_uncacheable_timeout(hosted, monkeypatch):
    hosted.settings.live_request_timeout_seconds = 0.01

    async def slow_center(tenant_id):
        await asyncio.sleep(0.1)
        raise AssertionError("The timed-out request should be cancelled")

    monkeypatch.setattr(hosted.real.connections, "center", slow_center)
    async with client(hosted) as api:
        response = await api.get("/api/v1/connections", headers=headers())
    assert response.status_code == 504
    assert response.json() == {
        "detail": "The connected source timed out. Try fewer sources and retry.",
    }
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("failure", ["http", "transport", "invalid-json"])
async def test_provider_failures_do_not_retain_or_log_private_payloads(caplog, failure):
    secret = "private-account-and-api-key"
    payload = "customer-business-data-do-not-log"

    def fail(request):
        if failure == "transport":
            raise httpx.ConnectError(f"{secret} {payload}", request=request)
        return httpx.Response(
            403 if failure == "http" else 200,
            text=f"{secret} {payload}",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as api:
        with pytest.raises((IntegrationAPIError, IntegrationTransportError)) as caught:
            await request_json(api, "openai", "POST", f"https://example.test/{secret}?key={secret}")
    assert secret not in caplog.text + str(caught.value)
    assert payload not in caplog.text + str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    if isinstance(caught.value, IntegrationAPIError):
        assert caught.value.detail is None


def test_integration_error_legacy_detail_argument_is_sanitized():
    error = IntegrationAPIError("openai", 401, "Rejected", detail="sensitive-provider-body")
    assert error.detail == "HTTP 401"
    assert "sensitive-provider-body" not in repr(error.__dict__)


async def test_provider_failure_keeps_only_allowlisted_enum_and_operation(caplog):
    secret = "private-customer-id-and-message"

    def fail(_request):
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": secret,
                    "details": [{"errors": [{"errorCode": {
                        "authorizationError": "USER_PERMISSION_DENIED"
                    }, "trigger": secret}]}],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as api:
        with pytest.raises(IntegrationAPIError) as caught:
            await request_json(
                api,
                "google_ads",
                "POST",
                "https://example.test",
                operation="account_metadata",
                safe_error_code_keys=frozenset({"authorizationError"}),
            )
    assert caught.value.operation == "account_metadata"
    assert caught.value.error_code == "USER_PERMISSION_DENIED"
    assert secret not in caplog.text + str(caught.value) + repr(caught.value.__dict__)
