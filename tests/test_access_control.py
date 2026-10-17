import httpx
import pytest

from backend.api.app import create_app
from backend.config import Settings
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


def app_with(**overrides):
    runtime = build_demo_runtime()
    return create_app(
        runtime=runtime,
        founder_experience=build_demo_founder_experience(runtime),
        settings=Settings(_env_file=None, **overrides),
    )


def client(app, host: str = "127.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(host, 50000)), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_token_is_required_when_configured() -> None:
    app = app_with(api_access_token="s3cret-token-value")
    async with client(app) as api:
        assert (await api.get("/health")).status_code == 200
        assert (await api.get("/api/v1/connections")).status_code == 401
        wrong = await api.get("/api/v1/connections", headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = await api.get(
            "/api/v1/connections", headers={"Authorization": "Bearer s3cret-token-value"}
        )
        assert ok.status_code == 200
        preflight = await api.options(
            "/api/v1/connections",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code == 200


@pytest.mark.asyncio
async def test_without_token_only_direct_loopback_is_served() -> None:
    app = app_with()
    async with client(app, host="203.0.113.7") as remote:
        assert (await remote.get("/api/v1/dashboard")).status_code == 401
    async with client(app) as local:
        proxied = await local.get("/api/v1/dashboard", headers={"X-Forwarded-For": "198.51.100.2"})
        assert proxied.status_code == 401
        assert (await local.get("/api/v1/dashboard")).status_code == 200


@pytest.mark.asyncio
async def test_public_demo_only_refuses_live_routes() -> None:
    app = app_with(public_demo_only=True)
    async with client(app) as api:
        assert (await api.get("/api/v1/connections")).status_code == 403
        assert (await api.get("/api/v1/audit-log?mode=live")).status_code == 403
        assert (await api.post("/api/v1/growth-intelligence/live")).status_code == 403
        scan = await api.post(
            "/api/v1/geo/scans",
            json={
                "brand": "Acme",
                "website": "https://acme.example.com",
                "category": "AI customer support",
                "mode": "live",
            },
        )
        assert scan.status_code == 403
        assert (await api.get("/api/v1/dashboard")).status_code == 200


@pytest.mark.asyncio
async def test_token_holder_is_recorded_as_the_approver() -> None:
    app = app_with(api_access_token="s3cret-token-value", api_principal_name="Jack")
    headers = {"Authorization": "Bearer s3cret-token-value"}
    async with client(app) as api:
        dashboard = (await api.get("/api/v1/dashboard", headers=headers)).json()
        run_id = next(
            item["workflow_run_id"]
            for item in dashboard["recommendations"]
            if item["workflow_run_id"]
        )
        run = (await api.get(f"/api/v1/workflows/{run_id}", headers=headers)).json()
        decided = await api.post(
            f"/api/v1/workflows/{run_id}/decision",
            headers=headers,
            json={
                "outcome": "approve",
                "actor": "someone-else",
                "action_digest": run["approval"]["action_digest"],
            },
        )
    assert decided.status_code == 200
    assert decided.json()["approval"]["decided_by"] == "Jack (API token)"
