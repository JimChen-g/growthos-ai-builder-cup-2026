import json
from decimal import Decimal

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from backend.api.app import create_app
from backend.config import Settings
from backend.models.connectors import (
    ConnectorProvider,
    DataMode,
    FeedbackCollectionRequest,
    GeoScanRequest,
)
from backend.services.connector_security import EncryptedCredentialVault
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime
from backend.services.real_intelligence import build_real_intelligence_runtime
from integrations.geo_providers import (
    GeminiSearchConfig,
    GeminiSearchProvider,
    OpenAIWebSearchConfig,
    OpenAIWebSearchProvider,
    PerplexitySonarConfig,
    PerplexitySonarProvider,
    VertexAISearchConfig,
    VertexAISearchProvider,
)


def _settings(tmp_path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        connection_vault_key=SecretStr(Fernet.generate_key().decode()),
        connection_vault_path=tmp_path / "credentials.enc",
        demo_intelligence_history_path=tmp_path / "demo-history.json",
        live_intelligence_history_path=tmp_path / "live-history.json",
        **overrides,
    )


@pytest.mark.asyncio
async def test_provider_adapters_parse_grounded_answers_and_citations() -> None:
    requests: list[httpx.Request] = []
    payloads = {
        "api.openai.com": {
            "output": [
                {"type": "web_search_call"},
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Intercom is one option.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.intercom.com/",
                                    "title": "Intercom",
                                }
                            ],
                        }
                    ],
                },
            ]
        },
        "generativelanguage.googleapis.com": {
            "status": "completed",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": "Intercom appears in the shortlist.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://www.intercom.com/help",
                                    "title": "Intercom Help",
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "aiplatform.googleapis.com": {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "Intercom is visible through Vertex AI."}],
                    },
                    "groundingMetadata": {
                        "groundingChunks": [
                            {
                                "web": {
                                    "uri": "https://www.intercom.com/customer-service",
                                    "title": "Intercom Customer Service",
                                }
                            }
                        ]
                    },
                }
            ]
        },
        "api.perplexity.ai": {
            "choices": [{"message": {"role": "assistant", "content": "Consider Intercom."}}],
            "citations": ["https://www.intercom.com/ai"],
            "search_results": [{"title": "Intercom AI", "url": "https://www.intercom.com/ai"}],
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payloads[request.url.host])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class StaticTokenProvider:
        async def access_token(self) -> str:
            return "vertex-oauth-token"

    providers = [
        OpenAIWebSearchProvider(OpenAIWebSearchConfig(api_key="openai-test-key"), client=client),
        GeminiSearchProvider(GeminiSearchConfig(api_key="gemini-test-key"), client=client),
        VertexAISearchProvider(
            VertexAISearchConfig(project_id="growthos-test-project"),
            client=client,
            token_provider=StaticTokenProvider(),
        ),
        PerplexitySonarProvider(
            PerplexitySonarConfig(api_key="perplexity-test-key"), client=client
        ),
    ]

    answers = [await provider.ask("Best customer support tools?") for provider in providers]
    await client.aclose()

    assert all("Intercom" in answer.answer for answer in answers)
    assert all(answer.citations for answer in answers)
    openai_body = json.loads(requests[0].content)
    assert openai_body["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert openai_body["model"] == "gpt-5.6-luna"
    assert openai_body["tool_choice"] == "required"
    assert openai_body["reasoning"] == {"effort": "low"}
    assert openai_body["text"] == {"verbosity": "low"}
    assert openai_body["max_output_tokens"] == 1600
    assert json.loads(requests[1].content)["tools"] == [{"type": "google_search"}]
    vertex_body = json.loads(requests[2].content)
    assert vertex_body["tools"] == [{"googleSearch": {}}]
    assert requests[2].headers["Authorization"] == "Bearer vertex-oauth-token"
    assert requests[2].url.path.endswith(
        "/projects/growthos-test-project/locations/global/publishers/google/"
        "models/gemini-2.5-flash:generateContent"
    )


@pytest.mark.asyncio
async def test_openai_adapter_explains_incomplete_textless_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "reasoning"}, {"type": "web_search_call"}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIWebSearchProvider(
        OpenAIWebSearchConfig(api_key="openai-test-key"),
        client=client,
    )

    with pytest.raises(
        RuntimeError,
        match="output limit was reached",
    ):
        await provider.ask("Best customer support tools?")
    await client.aclose()


@pytest.mark.asyncio
async def test_vertex_api_key_uses_the_selected_regional_endpoint() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Intercom is included."}]},
                        "groundingMetadata": {"groundingChunks": []},
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = VertexAISearchProvider(
        VertexAISearchConfig(
            project_id="growthos-regional-test",
            location="us-central1",
            api_key="vertex-api-key",
        ),
        client=client,
    )

    answer = await provider.ask("Best customer support tools?")
    await client.aclose()

    assert answer.answer == "Intercom is included."
    assert captured[0].url.host == "us-central1-aiplatform.googleapis.com"
    assert captured[0].headers["x-goog-api-key"] == "vertex-api-key"
    assert "Authorization" not in captured[0].headers


@pytest.mark.asyncio
async def test_encrypted_vault_is_tenant_scoped_and_never_stores_plaintext(tmp_path) -> None:
    vault = EncryptedCredentialVault(
        tmp_path / "vault.enc", SecretStr(Fernet.generate_key().decode())
    )
    secret = SecretStr("sk-live-secret-value")

    await vault.put("tenant-a", ConnectorProvider.OPENAI, secret)

    assert (await vault.get("tenant-a", ConnectorProvider.OPENAI)).get_secret_value() == (
        "sk-live-secret-value"
    )
    assert await vault.get("tenant-b", ConnectorProvider.OPENAI) is None
    assert b"sk-live-secret-value" not in (tmp_path / "vault.enc").read_bytes()


@pytest.mark.asyncio
async def test_geo_runner_labels_demo_and_refuses_to_fake_live_results(tmp_path) -> None:
    runtime = build_real_intelligence_runtime(_settings(tmp_path))
    request = GeoScanRequest(
        brand="Intercom",
        website="https://www.intercom.com",
        category="AI customer support",
        competitors=["Zendesk", "Ada"],
    )

    demo = await runtime.geo.scan("local-demo", request)
    live = await runtime.geo.scan("local-demo", request.model_copy(update={"mode": DataMode.LIVE}))

    assert demo.mode is DataMode.DEMO
    assert demo.overall_visibility_score == Decimal("60.0")
    assert "Synthetic" in demo.provenance
    assert live.mode is DataMode.LIVE
    assert live.overall_visibility_score == Decimal("0.0")
    assert all(item.error == "No credential is configured for live mode." for item in live.results)


@pytest.mark.asyncio
async def test_feedback_collector_marks_reddit_fallback_and_demo_sources(tmp_path) -> None:
    runtime = build_real_intelligence_runtime(_settings(tmp_path))

    response = await runtime.feedback.collect(
        "local-demo",
        FeedbackCollectionRequest(brand="Intercom", mode=DataMode.DEMO),
    )

    assert len(response.signals) == 2
    assert response.analysis is not None
    assert response.analysis.main_problem == "Onboarding difficulty"
    assert {item.collection_method for item in response.sources} == {"synthetic fixture"}


@pytest.mark.asyncio
async def test_connection_center_api_saves_secret_without_returning_it(tmp_path) -> None:
    phase2 = build_demo_runtime()
    phase3 = build_demo_founder_experience(phase2)
    settings = _settings(
        tmp_path,
        google_ads_access_token=SecretStr("google-access"),
        google_ads_developer_token=SecretStr("google-developer"),
        google_ads_customer_id="1234567890",
        meta_ads_access_token=SecretStr("meta-access"),
        meta_ads_ad_account_id="act_987654321",
        meta_ads_api_version="v26.0",
    )
    real = build_real_intelligence_runtime(settings)
    app = create_app(phase2, phase3, real)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        before = await client.get("/api/v1/connections")
        saved = await client.put(
            "/api/v1/connections/openai/credential",
            json={"secret": "sk-encrypted-user-key"},
        )
        saved_vertex = await client.put(
            "/api/v1/connections/gemini_vertex/config",
            json={
                "project_id": "growthos-vertex-test",
                "location": "global",
                "api_key": "vertex-secret-api-key",
            },
        )
        after = await client.get("/api/v1/connections")
        demo_scan = await client.post(
            "/api/v1/geo/scans",
            json={
                "brand": "Intercom",
                "website": "https://www.intercom.com",
                "category": "AI customer support",
                "competitors": ["Zendesk", "Ada"],
                "mode": "demo",
            },
        )
        saved_history = await client.get(
            "/api/v1/intelligence-history?mode=demo&kind=brand_visibility"
        )

    assert before.status_code == 200
    assert before.json()["vault_encryption_enabled"] is True
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert "sk-encrypted-user-key" not in saved.text
    assert saved_vertex.status_code == 200
    assert saved_vertex.json()["project_id"] == "growthos-vertex-test"
    assert saved_vertex.json()["location"] == "global"
    assert "vertex-secret-api-key" not in saved_vertex.text
    openai = next(item for item in after.json()["connections"] if item["provider"] == "openai")
    assert openai["configuration_source"] == "encrypted_vault"
    assert openai["demo_available"] is True
    assert openai["setup_kind"] == "api_key"
    assert len(openai["setup_steps"]) <= 3
    vertex = next(
        item for item in after.json()["connections"] if item["provider"] == "gemini_vertex"
    )
    assert vertex["configured"] is True
    assert vertex["configuration_source"] == "encrypted_vault"
    assert vertex["project_id"] == "growthos-vertex-test"
    assert vertex["setup_kind"] == "cloud_project"
    assert "vertex-secret-api-key" not in after.text
    ads = {
        item["provider"]: item
        for item in after.json()["connections"]
        if item["provider"] in {"google_ads", "meta_ads"}
    }
    assert ads["google_ads"]["selected_account_id"] == "1234567890"
    assert ads["meta_ads"]["selected_account_id"] == "987654321"
    assert demo_scan.status_code == 200
    assert demo_scan.json()["mode"] == "demo"
    assert saved_history.status_code == 200
    assert saved_history.json()["total"] == 1
    assert saved_history.json()["items"][0]["snapshot"]["result"] == demo_scan.json()


@pytest.mark.asyncio
async def test_connection_center_allows_browser_preflight_for_put_requests(tmp_path) -> None:
    phase2 = build_demo_runtime()
    phase3 = build_demo_founder_experience(phase2)
    real = build_real_intelligence_runtime(_settings(tmp_path))
    app = create_app(phase2, phase3, real)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.options(
            "/api/v1/connections/gemini_vertex/config",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-tenant-id",
            },
        )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
