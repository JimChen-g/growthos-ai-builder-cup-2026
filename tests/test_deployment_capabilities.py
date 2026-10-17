import httpx
import pytest

from backend.api.app import create_app
from backend.config import Settings
from backend.services.competition_decision import CompetitionDecisionService
from backend.services.competition_store import InMemoryCompetitionStore
from backend.services.phase2_runtime import build_demo_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("public_demo_only", [True, False])
@pytest.mark.parametrize("auth_mode", ["firebase", "token", "loopback"])
async def test_workspace_policy_is_public_without_exposing_server_secrets(
    public_demo_only: bool, auth_mode: str
) -> None:
    settings = Settings(
        _env_file=None,
        public_demo_only=public_demo_only,
        firebase_auth_required=auth_mode == "firebase",
        firebase_project_id="private-project" if auth_mode == "firebase" else None,
        api_access_token="private-access-token" if auth_mode == "token" else None,
    )
    app = create_app(settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 50000)),
        base_url="http://test",
    ) as api:
        response = await api.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "public_demo_only": public_demo_only,
            "live_available": not public_demo_only,
            "live_self_service_enabled": False,
        }
        assert response.headers["Cache-Control"] == "no-store"

        # Publishing deployment policy must not weaken account or business-data access.
        assert (await api.get("/api/v1/connections")).status_code == 401
        assert (await api.get("/api/v1/dashboard")).status_code == 401
        assert (await api.get("/api/v1/capabilities/private")).status_code == 401


@pytest.mark.asyncio
async def test_public_workspace_refuses_shared_state_but_keeps_sample_views(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.api.security._verify_firebase_token", lambda token, project: {"sub": token}
    )
    app = create_app(
        runtime=build_demo_runtime(),
        settings=Settings(
            _env_file=None,
            public_demo_only=True,
            firebase_auth_required=True,
            firebase_project_id="test-project",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer visitor-a"},
    ) as api:
        for method, path in [
            ("GET", "/api/v1/approvals"),
            ("GET", "/api/v1/workflows/shared-run"),
            ("GET", "/api/v1/workflows/shared-run/phase3-handoff"),
            ("POST", "/api/v1/workflows/demo"),
            ("POST", "/api/v1/workflows/shared-run/decision"),
            ("GET", "/api/v1/audit-log?mode=demo"),
            ("GET", "/api/v1/audit-proof?mode=demo"),
            ("GET", "/api/v1/audit-proof/status?mode=demo"),
            ("GET", "/api/v1/intelligence-history?mode=demo"),
            ("POST", "/api/v1/feedback/analyze"),
            ("POST", "/api/v1/feedback/collect"),
            ("POST", "/api/v1/geo/scans"),
        ]:
            assert (await api.request(method, path)).status_code == 403, path
        for path in [
            "/api/v1/founder/experience", "/api/v1/dashboard", "/api/v1/feedback/demo"
        ]:
            assert (await api.get(path)).status_code == 200, path


@pytest.mark.asyncio
async def test_public_competition_actions_remain_available_and_isolated(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.api.security._verify_firebase_token", lambda token, project: {"sub": token}
    )
    decisions = CompetitionDecisionService(
        store=InMemoryCompetitionStore(), generator=None, model_id="test-model",
        session_limit=20, global_limit=100, cache_ttl_seconds=3600, max_evidence_items=30,
    )
    app = create_app(
        competition_decisions=decisions,
        settings=Settings(
            _env_file=None, public_demo_only=True, firebase_auth_required=True,
            firebase_project_id="test-project",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer visitor-a"},
    ) as api:
        response = await api.post("/api/v1/competition/analyze", json={
            "founder_goal": "Review the sample acquisition problem.",
            "evidence": [
                {"evidence_id": "EV-CAC", "label": "CAC", "value": "$120", "source": "sample"},
                {"evidence_id": "EV-ROAS", "label": "ROAS", "value": "2.8", "source": "sample"},
            ],
        })
        assert response.status_code == 201
        decision = response.json()
        path = f"/api/v1/competition/workflows/{decision['workflow_id']}"
        body = {"outcome": "approve", "action_digest": decision["action_digest"]}
        other = {"Authorization": "Bearer visitor-b"}
        assert (await api.get(path, headers=other)).status_code == 404
        assert (await api.post(f"{path}/decision", headers=other, json=body)).status_code == 404
        approved = await api.post(f"{path}/decision", json=body)
        assert approved.status_code == 200
        assert approved.json()["approval_status"] == "executed"
