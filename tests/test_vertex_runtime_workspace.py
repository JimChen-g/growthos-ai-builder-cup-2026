"""Offline authorization of one expiring owner link to the Cloud Run identity.

All identities/records are fixtures; provider construction and calls never access ADC,
Firestore, cloud projects, or real credentials.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError

import backend.api.app as api_app
import backend.api.security as security
import backend.services.real_intelligence as services
from backend.config import Settings
from backend.models.connectors import ConnectorProvider
from backend.services.connector_security import CredentialMetadata, TenantQuotaLimiter
from backend.services.phase2_runtime import build_demo_runtime
from integrations.geo_providers import ProviderAnswer, VertexAISearchProvider

PROVIDER = ConnectorProvider.GEMINI_VERTEX
PROJECT = "offline-vertex-runtime-project"
OWNER = security.live_workspace_id_for_uid("fixture-owner")
FOREIGN = security.live_workspace_id_for_uid("fixture-judge")
MARKER = {"auth_mode": "runtime_identity", "project_id": PROJECT, "location": "global"}


class VaultFixture:
    enabled = True

    def __init__(self):
        self.records = {}
        self.lifecycle = {}

    async def get(self, tenant_id, provider):
        # Deliberately allow stale reads: the runtime-identity resolver must also
        # reject missing/expired/revoked lifecycle evidence after this read.
        return self.records.get((tenant_id, provider))

    async def metadata(self, tenant_id, provider):
        return self.lifecycle.get((tenant_id, provider), CredentialMetadata("missing"))

    async def put(self, tenant_id, provider, secret):
        self.records[(tenant_id, provider)] = secret
        now = datetime.now(UTC)
        self.lifecycle[(tenant_id, provider)] = CredentialMetadata(
            "saved", now, now + timedelta(days=7)
        )

    async def delete(self, tenant_id, provider):
        self.records.pop((tenant_id, provider), None)
        self.lifecycle.pop((tenant_id, provider), None)


@pytest.fixture
def linked_runtime(monkeypatch):
    settings = Settings(
        _env_file=None,
        firebase_auth_required=True,
        firebase_project_id=PROJECT,
        public_demo_only=True,
        live_self_service_enabled=True,
        live_storage_backend="firestore",
        live_vertex_runtime_workspace_id=OWNER,
        vertex_ai_project_id=PROJECT,
        vertex_ai_location="global",
        # A hosted runtime link must never start using any operator key.
        vertex_ai_api_key="fixture-operator-vertex-key-must-not-be-used",
        gemini_api_key="fixture-operator-gemini-key-must-not-be-used",
        openai_api_key="fixture-operator-openai-key-must-not-be-used",
    )
    vault = VaultFixture()
    resolver = services.ConnectorCredentialResolver(settings, vault)
    quota = TenantQuotaLimiter(3)
    factory = services.GeoProviderFactory(settings, resolver)
    runtime = services.RealIntelligenceRuntime(
        connections=services.ConnectionCenterService(settings, vault, resolver, quota),
        geo=services.GeoPromptRunner(factory, quota),
        feedback=services.FeedbackCollector(settings, resolver, quota),
    )
    clients = []

    class OfflineVertex:
        def __init__(self, config):
            self.config = config
            self.prompts = []
            self.closed = False
            clients.append(self)

        async def ask(self, prompt):
            self.prompts.append(prompt)
            return ProviderAnswer(answer="Fixture support software.", citations=[])

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(services, "VertexAISearchProvider", OfflineVertex)

    def verify(token, project_id):
        assert project_id == PROJECT and token in {"fixture-owner", "fixture-judge"}
        return {"sub": token, "email_verified": True,
                "firebase": {"sign_in_provider": "google.com"}}

    monkeypatch.setattr(security, "_verify_firebase_token", verify)
    app = api_app.create_app(
        runtime=build_demo_runtime(), real_intelligence=runtime, settings=settings
    )
    return SimpleNamespace(
        settings=settings, vault=vault, resolver=resolver, factory=factory,
        runtime=runtime, quota=quota, clients=clients, app=app,
    )


async def grant(linked, tenant=OWNER, **changes):
    await linked.vault.put(tenant, PROVIDER, SecretStr(json.dumps({**MARKER, **changes})))


def headers(identity="fixture-owner", tenant=FOREIGN):
    return {"Authorization": f"Bearer {identity}", "X-Tenant-ID": tenant}


def api_client(linked):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=linked.app), base_url="https://offline.test"
    )


async def test_valid_owner_link_is_configured_and_uses_only_project_runtime_identity(
    linked_runtime,
):
    linked = linked_runtime
    await grant(linked)
    connection = await linked.resolver.vertex_connection(OWNER)
    assert connection.runtime_identity
    assert connection.api_key is None
    assert connection.project_id == PROJECT and connection.location == "global"
    assert connection.source == "encrypted_vault"
    assert not linked.resolver.allows_server_credentials(OWNER)
    async with api_client(linked) as api:
        center = await api.get("/api/v1/connections", headers=headers())
        checked = await api.post("/api/v1/connections/gemini_vertex/test", headers=headers())
    assert center.status_code == 200 and center.json()["tenant_id"] == OWNER
    status = next(row for row in center.json()["connections"] if row["provider"] == PROVIDER)
    assert status["configured"] and status["storage_status"] == "saved"
    assert status["saved_at"] and status["expires_at"]
    assert status["auth_method"] == "Enterprise connection · Linked project identity"
    assert "auth_mode" not in center.text and "runtime_identity" not in center.text
    assert "fixture-operator" not in center.text + checked.text
    assert checked.status_code == 200 and checked.json()["success"]
    assert len(linked.clients) == 1 and linked.clients[0].closed
    assert len(linked.clients[0].prompts) == 1
    assert linked.clients[0].config.api_key is None
    assert linked.clients[0].config.project_id == PROJECT
    assert await linked.quota.used(OWNER, PROVIDER) == 1
    assert await linked.quota.used(FOREIGN, PROVIDER) == 0
    assert (await linked.resolver.secret(OWNER, ConnectorProvider.GEMINI)) == (None, None)
    assert (await linked.resolver.secret(OWNER, ConnectorProvider.OPENAI)) == (None, None)


async def test_owner_factory_uses_real_vertex_bearer_path_without_operator_key(
    linked_runtime, monkeypatch
):
    linked = linked_runtime
    await grant(linked)
    requests = []
    token_reads = []

    class TokenFixture:
        async def access_token(self):
            token_reads.append("read")
            return "fixture-short-lived-runtime-token"

    def handle(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.host == "aiplatform.googleapis.com"
        assert request.url.path == (
            f"/v1/projects/{PROJECT}/locations/global/publishers/google/"
            f"models/{linked.settings.vertex_ai_model}:generateContent"
        )
        assert request.headers["Authorization"] == "Bearer fixture-short-lived-runtime-token"
        assert "x-goog-api-key" not in request.headers
        assert "fixture-operator" not in str(request.headers) + request.content.decode()
        return httpx.Response(200, json={"candidates": [{
            "content": {"parts": [{"text": "Fixture grounded support answer."}]},
        }]})

    class BoundVertex(VertexAISearchProvider):
        def __init__(self, config):
            super().__init__(
                config,
                client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
                token_provider=TokenFixture(),
            )
            self._owns_client = True

    monkeypatch.setattr(services, "VertexAISearchProvider", BoundVertex)
    result = await linked.runtime.geo.connection_test(OWNER, PROVIDER)
    assert result.success and result.checked_live
    assert len(requests) == len(token_reads) == 1
    foreign = await linked.runtime.geo.connection_test(FOREIGN, PROVIDER)
    assert not foreign.configured and not foreign.checked_live
    assert len(requests) == len(token_reads) == 1


@pytest.mark.parametrize("clone_marker", [False, True])
async def test_foreign_identity_cannot_use_owner_link_or_forged_tenant_header(
    linked_runtime, clone_marker
):
    linked = linked_runtime
    await grant(linked)
    if clone_marker:
        await grant(linked, FOREIGN)
    async with api_client(linked) as api:
        result = await api.post(
            "/api/v1/connections/gemini_vertex/test",
            headers=headers("fixture-judge", OWNER),
        )
    assert result.status_code == 200
    assert not result.json()["configured"] and not result.json()["checked_live"]
    assert await linked.resolver.vertex_connection(FOREIGN) is None
    assert linked.clients == []
    assert await linked.quota.used(OWNER, PROVIDER) == 0
    assert await linked.quota.used(FOREIGN, PROVIDER) == 0


@pytest.mark.parametrize("setting,value", [
    ("live_vertex_runtime_workspace_id", None),
    ("live_vertex_runtime_workspace_id", FOREIGN),
    ("firebase_auth_required", False),
    ("live_self_service_enabled", False),
    ("live_storage_backend", "local"),
    ("firebase_project_id", "another-project"),
    ("vertex_ai_project_id", "another-project"),
    ("vertex_ai_location", "us-central1"),
])
async def test_runtime_link_requires_every_server_side_scope_guard(linked_runtime, setting, value):
    linked = linked_runtime
    await grant(linked)
    setattr(linked.settings, setting, value)
    assert await linked.factory.build(OWNER, PROVIDER) is None
    assert linked.clients == []


@pytest.mark.parametrize("changes", [
    {"auth_mode": "adc"},
    {"auth_mode": None},
    {"project_id": "another-project"},
    {"location": "us-central1"},
    {"api_key": "fixture-conflicting-key"},
    {"owner": "fixture-owner"},
])
async def test_runtime_marker_cannot_select_other_projects_locations_or_credentials(
    linked_runtime, changes
):
    linked = linked_runtime
    await grant(linked, **changes)
    assert await linked.factory.build(OWNER, PROVIDER) is None
    assert linked.clients == []


async def test_missing_or_unmarked_owner_record_never_falls_back_to_server_adc(linked_runtime):
    linked = linked_runtime
    assert await linked.resolver.vertex_connection(OWNER) is None
    await linked.vault.put(
        OWNER, PROVIDER, SecretStr(json.dumps({"project_id": PROJECT, "location": "global"}))
    )
    assert await linked.factory.build(OWNER, PROVIDER) is None
    assert linked.clients == []


@pytest.mark.parametrize("condition", [
    "missing", "expired", "unavailable", "no-timestamps", "naive-timestamps",
    "future-save", "past-expiry", "over-seven-days", "past-seven-day-bound", "vault-disabled",
])
async def test_runtime_link_requires_current_seven_day_lifecycle(linked_runtime, condition):
    linked = linked_runtime
    await grant(linked)
    now = datetime.now(UTC)
    metadata = CredentialMetadata("saved", now - timedelta(hours=1), now + timedelta(days=6))
    if condition in {"missing", "expired", "unavailable"}:
        metadata = CredentialMetadata(condition)
    elif condition == "no-timestamps":
        metadata = CredentialMetadata("saved")
    elif condition == "naive-timestamps":
        metadata = CredentialMetadata("saved", now.replace(tzinfo=None), now.replace(tzinfo=None))
    elif condition == "future-save":
        metadata = CredentialMetadata("saved", now + timedelta(hours=1), now + timedelta(days=1))
    elif condition == "past-expiry":
        metadata = CredentialMetadata("saved", now - timedelta(days=8), now - timedelta(days=1))
    elif condition == "over-seven-days":
        metadata = CredentialMetadata("saved", now, now + timedelta(days=8))
    elif condition == "past-seven-day-bound":
        # The subsecond write-drift tolerance must not extend the authorization period.
        metadata = CredentialMetadata(
            "saved", now - timedelta(days=7, milliseconds=250), now + timedelta(milliseconds=500)
        )
    elif condition == "vault-disabled":
        linked.vault.enabled = False
    linked.vault.lifecycle[(OWNER, PROVIDER)] = metadata
    assert await linked.factory.build(OWNER, PROVIDER) is None
    assert linked.clients == []


async def test_owner_disconnect_revokes_runtime_link_without_environment_fallback(linked_runtime):
    linked = linked_runtime
    await grant(linked)
    async with api_client(linked) as api:
        disconnected = await api.delete("/api/v1/connections/gemini_vertex", headers=headers())
        checked = await api.post("/api/v1/connections/gemini_vertex/test", headers=headers())
    assert disconnected.status_code == 200 and not disconnected.json()["configured"]
    assert checked.status_code == 200 and not checked.json()["configured"]
    assert linked.settings.live_vertex_runtime_workspace_id == OWNER
    assert await linked.resolver.vertex_connection(OWNER) is None
    assert linked.clients == []


@pytest.mark.parametrize("identity", ["fixture-owner", "fixture-judge"])
async def test_public_configuration_cannot_create_runtime_marker_or_keyless_link(
    linked_runtime, identity
):
    linked = linked_runtime
    await grant(linked)
    original = await linked.vault.get(OWNER, PROVIDER)
    async with api_client(linked) as api:
        forged = await api.put(
            "/api/v1/connections/gemini_vertex/config", headers=headers(identity), json=MARKER
        )
        keyless = await api.put(
            "/api/v1/connections/gemini_vertex/config", headers=headers(identity),
            json={"project_id": PROJECT, "location": "global"},
        )
    assert forged.status_code == 422
    assert keyless.status_code == 409 and "own Vertex AI API key" in keyless.text
    assert await linked.vault.get(OWNER, PROVIDER) == original
    assert await linked.vault.get(FOREIGN, PROVIDER) is None
    assert linked.clients == []


async def test_judges_can_still_use_their_own_vertex_api_key(linked_runtime):
    linked = linked_runtime
    await linked.vault.put(FOREIGN, PROVIDER, SecretStr(json.dumps({
        "project_id": "judges-own-project", "location": "us-central1",
        "api_key": "fixture-judges-own-vertex-key",
    })))
    connection = await linked.resolver.vertex_connection(FOREIGN)
    assert not connection.runtime_identity
    assert connection.api_key.get_secret_value() == "fixture-judges-own-vertex-key"
    assert connection.project_id == "judges-own-project"


@pytest.mark.parametrize(
    "value", ["fixture-owner", "*", "owner@example.test", OWNER + "," + FOREIGN]
)
def test_runtime_workspace_setting_accepts_only_one_opaque_server_workspace(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, live_vertex_runtime_workspace_id=value)


def test_runtime_workspace_setting_is_disabled_by_default_or_blank():
    assert Settings(_env_file=None).live_vertex_runtime_workspace_id is None
    blank = Settings(_env_file=None, live_vertex_runtime_workspace_id=" ")
    assert blank.live_vertex_runtime_workspace_id is None
