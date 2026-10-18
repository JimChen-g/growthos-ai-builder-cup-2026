"""Offline Live acceptance through real adapters, aggregation, and hosted API guards.

Provider HTTP responses below are deliberate test fixtures, not real-account acceptance.
No demo report is relabelled as Live and no owner credentials or external service is used.
"""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.fernet import Fernet

import backend.api.app as api_app
import backend.api.security as security
import backend.services.growth_intelligence as growth
from backend.config import Settings
from backend.models.audit_log import AgentActionStatus
from backend.models.connectors import ConnectorProvider, DataMode
from backend.services.audit_log import AuditLogService
from backend.services.phase2_runtime import build_demo_runtime
from backend.services.real_intelligence import build_real_intelligence_runtime

AsyncClient = httpx.AsyncClient
# As-of date: the reporting window ends on the preceding complete calendar day.
TODAY = date(2026, 2, 1)
SECRETS = {
    "stripe": "rk_test_judge_a_stripe_only",
    "google": "test_judge_a_google_access_only",
    "developer": "test_judge_a_google_developer_only",
    "client": "test_judge_a_google_client",
    "client_secret": "test_judge_a_google_client_secret_only",
    "refresh": "test_judge_a_google_refresh_only",
    "meta": "test_judge_a_meta_access_only",
}
PROVIDERS = (ConnectorProvider.STRIPE, ConnectorProvider.GOOGLE_ADS, ConnectorProvider.META_ADS)


def auth(identity="judge-a", tenant="local-demo"):
    return {"Authorization": f"Bearer {identity}", "X-Tenant-ID": tenant}


def workspace(identity="judge-a"):
    return security.live_workspace_id_for_uid(identity)


class ProviderFixtures:
    def __init__(self):
        self.requests = []
        self.configs = {}
        self.fail_provider = None

    def handle(self, request):
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        provider = {
            "api.stripe.com": "stripe",
            "googleads.googleapis.com": "google_ads",
            "graph.facebook.com": "meta_ads",
            "oauth2.googleapis.com": "google_oauth",
        }.get(host)
        assert provider, f"Unexpected outbound endpoint: {host}"
        if self.fail_provider == provider:
            return httpx.Response(
                401,
                json={
                    "error": {
                        "message": "private-business-response " + " ".join(SECRETS.values()),
                    }
                },
            )
        if provider == "google_oauth":
            assert path == "/token" and request.method == "POST"
            form = parse_qs(request.content.decode())
            assert form == {
                "client_id": [SECRETS["client"]],
                "client_secret": [SECRETS["client_secret"]],
                "refresh_token": [SECRETS["refresh"]],
                "grant_type": ["refresh_token"],
            }
            return httpx.Response(200, json={"access_token": SECRETS["google"], "expires_in": 3600})
        expected = SECRETS[
            {"stripe": "stripe", "google_ads": "google", "meta_ads": "meta"}[provider]
        ]
        assert request.headers["Authorization"] == f"Bearer {expected}"
        if provider == "stripe":
            assert request.method == "GET"
            if path == "/v1/subscriptions":
                return httpx.Response(
                    200,
                    json={
                        "has_more": False,
                        "data": [
                            {
                                "id": "sub_fixture",
                                "status": "active",
                                "customer": {"discount": None},
                                "items": {
                                    "data": [
                                        {
                                            "quantity": 1,
                                            "price": {
                                                "currency": "usd",
                                                "unit_amount": 6900,
                                                "recurring": {
                                                    "interval": "month",
                                                    "interval_count": 1,
                                                },
                                            },
                                        },
                                        {
                                            "quantity": 1,
                                            "price": {
                                                "currency": "usd",
                                                "unit_amount": 37200,
                                                "recurring": {
                                                    "interval": "year",
                                                    "interval_count": 1,
                                                },
                                            },
                                        },
                                    ]
                                },
                            },
                        ],
                    },
                )
            assert path == "/v1/charges"
            current = (
                datetime.fromtimestamp(int(request.url.params["created[gte]"]), UTC).month == 1
            )
            charges = (
                [
                    {
                        "id": "ch_a",
                        "paid": True,
                        "currency": "usd",
                        "amount_captured": 27000,
                        "amount_refunded": 3000,
                        "customer": "fixture_customer_a",
                    },
                    {
                        "id": "ch_b",
                        "paid": True,
                        "currency": "usd",
                        "amount_captured": 16000,
                        "amount_refunded": 0,
                        "customer": "fixture_customer_b",
                    },
                ]
                if current
                else [
                    {
                        "id": "ch_prior",
                        "paid": True,
                        "currency": "usd",
                        "amount_captured": 20000,
                        "amount_refunded": 0,
                        "customer": "fixture_customer_a",
                    },
                ]
            )
            return httpx.Response(200, json={"has_more": False, "data": charges})
        if provider == "google_ads":
            assert request.method == "POST"
            assert "/customers/1234567890/" in path
            # Legacy stored tokens remain readable but must not be sent after sunset.
            assert "developer-token" not in request.headers
            assert request.headers["login-customer-id"] == "9988776655"
            query = json.loads(request.content)["query"]
            if "FROM customer" in query:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "customer": {
                                    "id": "1234567890",
                                    "currencyCode": "USD",
                                    "timeZone": "UTC",
                                }
                            },
                        ]
                    },
                )
            assert path.endswith("googleAds:searchStream")
            current = "2026-01-01" in query
            metrics = {
                "costMicros": "120000000" if current else "80000000",
                "clicks": "40",
                "conversions": "4",
                "conversionsValue": "240",
            }
            return httpx.Response(
                200,
                json=[
                    {
                        "results": [
                            {
                                "campaign": {
                                    "id": "fixture_google",
                                    "name": "Fixture Google Campaign",
                                    "status": "ENABLED",
                                },
                                "metrics": metrics,
                            }
                        ]
                    }
                ],
            )
        assert request.method == "GET"
        assert path.startswith("/v26.0/act_24680")
        if not path.endswith("/insights"):
            return httpx.Response(
                200, json={"id": "act_24680", "currency": "USD", "timezone_name": "UTC"}
            )
        period = json.loads(request.url.params["time_range"])
        current = period["since"] == "2026-01-01"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "campaign_id": "fixture_meta",
                        "campaign_name": "Fixture Meta Campaign",
                        "ad_id": "fixture_ad",
                        "ad_name": "Fixture Creative",
                        "spend": "80",
                        "clicks": "20",
                        "actions": [{"action_type": "purchase", "value": "2"}],
                        "action_values": [
                            {"action_type": "purchase", "value": "320" if current else "240"}
                        ],
                    }
                ]
            },
        )


@pytest.fixture
def acceptance(tmp_path, monkeypatch):
    selected = Settings(
        _env_file=None,
        firebase_project_id="offline-live-acceptance",
        firebase_auth_required=True,
        public_demo_only=True,
        live_self_service_enabled=True,
        live_storage_backend="local",
        connection_vault_key=Fernet.generate_key().decode(),
        connection_vault_path=tmp_path / "test-vault.enc",
        analysis_days=31,
        market_demand_score=60,
        execution_capability_score=62.5,
        stripe_secret_key="rk_test_foreign_stripe_do_not_use",
        google_ads_access_token="foreign-google-do-not-use",
        google_ads_developer_token="foreign-developer-do-not-use",
        google_ads_customer_id="777",
        google_ads_client_id=None,
        google_ads_client_secret=None,
        google_ads_refresh_token=None,
        meta_ads_access_token="foreign-meta-do-not-use",
        meta_ads_ad_account_id="888",
        meta_ads_api_version="v26.0",
    )
    local = selected.model_copy(
        update={
            "firebase_auth_required": False,
            "live_self_service_enabled": False,
        }
    )
    runtime = build_real_intelligence_runtime(local)
    runtime.connections.settings = selected
    runtime.connections.resolver.settings = selected

    def verify(token, project_id):
        assert project_id == "offline-live-acceptance" and token in {"judge-a", "judge-b"}
        return {
            "sub": token,
            "email_verified": True,
            "firebase": {"sign_in_provider": "google.com"},
        }

    monkeypatch.setattr(security, "_verify_firebase_token", verify)
    ledgers = {}

    def ledger_factory(project, database, owner, retention):
        return ledgers.setdefault(owner, AuditLogService(mode=DataMode.LIVE))

    monkeypatch.setattr(api_app, "FirestoreAuditLogService", ledger_factory)
    fixtures = ProviderFixtures()

    def install_adapter(name, provider):
        original = getattr(growth, name)

        class BoundAdapter(original):
            def __init__(self, config):
                fixtures.configs[provider] = config
                super().__init__(
                    config, client=AsyncClient(transport=httpx.MockTransport(fixtures.handle))
                )
                self._owns_client = True

        monkeypatch.setattr(growth, name, BoundAdapter)

    for name, provider in (
        ("StripeIntegration", "stripe"),
        ("GoogleAdsIntegration", "google_ads"),
        ("MetaAdsIntegration", "meta_ads"),
    ):
        install_adapter(name, provider)

    async def fixed_date_run(settings):
        return await growth.run_growth_intelligence(settings, today=TODAY)

    monkeypatch.setattr(api_app, "run_growth_intelligence", fixed_date_run)
    app = api_app.create_app(
        runtime=build_demo_runtime(), real_intelligence=runtime, settings=selected
    )
    return SimpleNamespace(app=app, runtime=runtime, fixtures=fixtures, ledgers=ledgers)


def client(acceptance):
    return AsyncClient(
        transport=httpx.ASGITransport(app=acceptance.app, client=("203.0.113.6", 1234)),
        base_url="https://offline.test",
    )


async def save_accounts(api, google_auth="access"):
    google = {
        "developer_token": SECRETS["developer"],
        "customer_id": "123-456-7890",
        "login_customer_id": "998-877-6655",
        "currency": "USD",
    }
    google.update(
        {"access_token": SECRETS["google"]}
        if google_auth == "access"
        else {
            "client_id": SECRETS["client"],
            "client_secret": SECRETS["client_secret"],
            "refresh_token": SECRETS["refresh"],
        }
    )
    for path, payload in (
        ("stripe/credential", {"secret": SECRETS["stripe"], "currency": "USD"}),
        ("google_ads/config", google),
        (
            "meta_ads/config",
            {
                "access_token": SECRETS["meta"],
                "ad_account_id": "act_24680",
                "api_version": "v26.0",
                "currency": "USD",
            },
        ),
    ):
        response = await api.put(f"/api/v1/connections/{path}", headers=auth(), json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["currency"] == "USD"
        assert all(secret not in response.text for secret in SECRETS.values())


@pytest.mark.parametrize("google_auth", ["access", "oauth"])
async def test_own_connections_flow_through_real_adapters_into_live_metrics(
    acceptance, google_auth
):
    async with client(acceptance) as api:
        await save_accounts(api, google_auth)
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
        before_foreign_request = len(acceptance.fixtures.requests)
        foreign = await api.post(
            "/api/v1/growth-intelligence/live",
            headers=auth("judge-b", workspace()),
        )
        ledger = await api.get("/api/v1/audit-log?mode=live", headers=auth())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data_source_mode"] == "live"
    assert payload["audit_recorded"] is True
    assert payload["company"] is None
    assert payload["revenue"]["period"] == {"start": "2026-01-01", "end": "2026-01-31"}
    assert payload["period_policy"] == "common_complete_calendar_days"
    assert payload["revenue"]["reporting_timezone"] == "UTC"
    assert payload["google_ads"]["reporting_timezone"] == "UTC"
    assert payload["meta_ads"]["reporting_timezone"] == "UTC"
    assert Decimal(payload["revenue"]["revenue"]) == 400
    assert Decimal(payload["revenue"]["mrr"]) == 100
    assert payload["revenue"]["customers"] == 2
    assert Decimal(payload["revenue"]["growth_rate_pct"]) == 100
    assert Decimal(payload["google_ads"]["spend"]) == 120
    assert Decimal(payload["google_ads"]["roas"]) == 2
    assert Decimal(payload["google_ads"]["cac"]) == 30
    assert Decimal(payload["meta_ads"]["spend"]) == 80
    assert Decimal(payload["meta_ads"]["roas"]) == 4
    assert Decimal(payload["meta_ads"]["cac"]) == 40
    # Google efficiency76.7, Meta100; spend-weighted86.02 =>25.8 contribution.
    assert Decimal(payload["growth_score"]["components"]["marketing_efficiency"]) == Decimal(
        "86.02"
    )
    assert Decimal(payload["growth_score"]["score"]) == Decimal("80.3")
    assert payload["growth_score"]["band"] == "strong"
    assert payload["growth_score"]["status"] == "available"
    assert payload["growth_score"]["missing_components"] == []
    assert ledger.json()["total"] == 1
    assert ledger.json()["entries"][0]["status"] == "executed"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert foreign.status_code == 409
    assert len(acceptance.fixtures.requests) == before_foreign_request
    assert all(
        secret not in response.text + foreign.text + ledger.text for secret in SECRETS.values()
    )
    assert "foreign-" not in response.text
    for provider in PROVIDERS:
        assert await acceptance.runtime.connections.quota.used(workspace(), provider) == 1
        assert await acceptance.runtime.connections.quota.used(workspace("judge-b"), provider) == 0
    oauth_requests = [
        r for r in acceptance.fixtures.requests if r.url.host == "oauth2.googleapis.com"
    ]
    assert len(oauth_requests) == (1 if google_auth == "oauth" else 0)


@pytest.mark.parametrize("empty_case, missing", [
    ("ads", ["marketing_efficiency"]),
    ("revenue_baseline", ["revenue_momentum"]),
    ("all", ["revenue_momentum", "marketing_efficiency"]),
])
async def test_live_empty_data_is_not_a_risk_score_and_keeps_historical_audit(
    acceptance, empty_case, missing
):
    original_handle = acceptance.fixtures.handle

    def handle(request):
        response = original_handle(request)
        host, path = request.url.host, request.url.path
        if empty_case in {"ads", "all"}:
            if host == "googleads.googleapis.com" and path.endswith("googleAds:searchStream"):
                return httpx.Response(200, json=[{"results": []}])
            if host == "graph.facebook.com" and path.endswith("/insights"):
                return httpx.Response(200, json={"data": []})
        if host == "api.stripe.com":
            if empty_case == "all":
                return httpx.Response(200, json={"data": [], "has_more": False})
            if empty_case == "revenue_baseline" and path == "/v1/charges":
                month = datetime.fromtimestamp(int(request.url.params["created[gte]"]), UTC).month
                if month != 1:
                    return httpx.Response(200, json={"data": [], "has_more": False})
        return response

    acceptance.fixtures.handle = handle
    async with client(acceptance) as api:
        await save_accounts(api)
        await api.get("/api/v1/audit-log?mode=live", headers=auth())
        old = await acceptance.ledgers[workspace()].record(
            agent="Growth Intelligence Engine", action="Measured Growth Score 39.5/100",
            reason="Historical measurement before explicit availability gating.",
            status=AgentActionStatus.EXECUTED,
        )
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
        ledger = await api.get("/api/v1/audit-log?mode=live", headers=auth())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data_source_mode"] == "live" and payload["company"] is None
    assert payload["audit_recorded"] is True
    result = payload["growth_score"]
    assert result["status"] == "insufficient_data"
    assert result["score"] is None and result["band"] is None
    assert result["missing_components"] == missing
    for component in missing:
        assert result["components"][component] is None
        assert result["contributions"][component] is None
    if empty_case in {"ads", "all"}:
        assert Decimal(payload["google_ads"]["spend"]) == 0
        assert Decimal(payload["meta_ads"]["spend"]) == 0
    if empty_case == "all":
        assert Decimal(payload["revenue"]["revenue"]) == 0
    entries = ledger.json()["entries"]
    assert ledger.json()["total"] == 2
    assert entries[0]["action"] == "Read business data; growth score unavailable"
    assert entries[0]["status"] == "executed"
    assert "No growth score or risk band was assigned." in entries[0]["reason"]
    assert entries[1] == old.model_dump(mode="json")
    assert "None/100" not in ledger.text and "null/100" not in ledger.text
    for provider in PROVIDERS:
        assert await acceptance.runtime.connections.quota.used(workspace(), provider) == 1
    assert all(secret not in response.text + ledger.text for secret in SECRETS.values())


@pytest.mark.parametrize("partial", [False, True])
async def test_missing_connections_do_not_spend_quota_or_call_providers(acceptance, partial):
    async with client(acceptance) as api:
        if partial:
            saved = await api.put(
                "/api/v1/connections/stripe/credential",
                headers=auth(),
                json={"secret": SECRETS["stripe"]},
            )
            assert saved.status_code == 200
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
    assert response.status_code == 409
    assert "missing configuration" in response.text
    assert acceptance.fixtures.requests == []
    assert acceptance.ledgers == {}
    for provider in PROVIDERS:
        assert await acceptance.runtime.connections.quota.used(workspace(), provider) == 0


@pytest.mark.parametrize("provider", ["stripe", "google_ads", "meta_ads"])
async def test_provider_rejection_is_safe_and_never_a_successful_report(
    acceptance,
    provider,
    caplog,
):
    acceptance.fixtures.fail_provider = provider
    async with client(acceptance) as api:
        await save_accounts(api)
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
        ledger = await api.get("/api/v1/audit-log?mode=live", headers=auth())
    assert response.status_code == 502
    assert "growth_score" not in response.json()
    assert ledger.json()["total"] == 0
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "private-business-response" not in response.text + caplog.text
    assert all(secret not in response.text + caplog.text for secret in SECRETS.values())


@pytest.mark.parametrize("failure_stage", ["construct", "record"])
async def test_valid_report_survives_audit_storage_outage_without_claiming_recorded(
    acceptance,
    monkeypatch,
    caplog,
    failure_stage,
):
    class UnavailableAudit:
        async def record(self, **kwargs):
            raise RuntimeError("private-audit-storage-diagnostic")

    def unavailable_factory(*args):
        if failure_stage == "construct":
            raise RuntimeError("private-audit-storage-diagnostic")
        return UnavailableAudit()

    monkeypatch.setattr(api_app, "FirestoreAuditLogService", unavailable_factory)
    async with client(acceptance) as api:
        await save_accounts(api)
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
    assert response.status_code == 200, response.text
    assert response.json()["data_source_mode"] == "live"
    assert response.json()["audit_recorded"] is False
    assert Decimal(response.json()["revenue"]["revenue"]) == 400
    assert Decimal(response.json()["growth_score"]["score"]) == Decimal("80.3")
    assert "private-audit-storage-diagnostic" not in response.text + caplog.text
    assert acceptance.ledgers == {}


async def test_exhausted_quota_is_safe_429_and_does_not_contact_providers(acceptance):
    acceptance.runtime.connections.quota.daily_limit = 1
    for provider in PROVIDERS:
        await acceptance.runtime.connections.quota.consume(workspace(), provider)
    async with client(acceptance) as api:
        await save_accounts(api)
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
    assert response.status_code == 429
    assert response.json() == {"detail": "Daily provider usage limit reached."}
    assert acceptance.fixtures.requests == []
    assert acceptance.ledgers == {}


async def test_hanging_audit_is_cancelled_and_successful_metrics_remain_visible(
    acceptance,
    monkeypatch,
):
    cancelled = asyncio.Event()

    class HangingAudit:
        async def record(self, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(api_app, "FirestoreAuditLogService", lambda *args: HangingAudit())
    async with client(acceptance) as api:
        await save_accounts(api)
        response = await asyncio.wait_for(
            api.post("/api/v1/growth-intelligence/live", headers=auth()),
            timeout=6,
        )
    assert cancelled.is_set()
    assert response.status_code == 200
    assert response.json()["audit_recorded"] is False
    assert Decimal(response.json()["revenue"]["revenue"]) == 400
    assert Decimal(response.json()["growth_score"]["score"]) == Decimal("80.3")
    assert acceptance.ledgers == {}


async def test_mixed_ads_currencies_fail_before_quota_or_external_requests(acceptance):
    async with client(acceptance) as api:
        await save_accounts(api)
        updated = await api.put(
            "/api/v1/connections/meta_ads/config",
            headers=auth(),
            json={
                "access_token": SECRETS["meta"],
                "ad_account_id": "act_24680",
                "api_version": "v26.0",
                "currency": "EUR",
            },
        )
        assert updated.status_code == 200
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
    assert response.status_code == 409
    assert "requires matching Google Ads and Meta Ads currencies" in response.json()["detail"]
    assert "automatic currency conversion is not available" in response.json()["detail"]
    assert acceptance.fixtures.requests == []
    assert acceptance.ledgers == {}
    for provider in PROVIDERS:
        assert await acceptance.runtime.connections.quota.used(workspace(), provider) == 0


async def test_aggregation_refuses_drifted_snapshot_currency_after_provider_reads(
    acceptance,
    monkeypatch,
):
    actual_meta_adapter = growth.MetaAdsIntegration

    class DriftedMetaAdapter(actual_meta_adapter):
        async def collect(self, period):
            snapshot = await super().collect(period)
            # Model an adapter regression: saved/verified USD, returned EUR snapshot.
            return snapshot.model_copy(update={"currency": "EUR"})

    monkeypatch.setattr(growth, "MetaAdsIntegration", DriftedMetaAdapter)
    async with client(acceptance) as api:
        await save_accounts(api)
        response = await api.post("/api/v1/growth-intelligence/live", headers=auth())
    assert response.status_code == 409
    assert "requires matching Google Ads and Meta Ads currencies" in response.json()["detail"]
    assert "growth_score" not in response.json()
    assert acceptance.fixtures.requests
    assert acceptance.ledgers == {}
    for provider in PROVIDERS:
        assert await acceptance.runtime.connections.quota.used(workspace(), provider) == 1
