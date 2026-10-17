import httpx
import pytest

import backend.api.app as api_app
from backend.config import Settings
from backend.services.growth_intelligence import (
    DataSourceMode,
    GrowthSourceError,
    run_growth_intelligence,
)
from backend.services.phase2_runtime import build_demo_runtime
from integrations.base import IntegrationAPIError


@pytest.mark.asyncio
async def test_live_dashboard_refresh_uses_live_growth_contract(monkeypatch) -> None:
    demo_report = await run_growth_intelligence(Settings(_env_file=None, data_source_mode="demo"))
    live_report = demo_report.model_copy(update={"data_source_mode": DataSourceMode.LIVE})

    async def fake_live_run(settings: Settings):
        assert settings.data_source_mode == "live"
        return live_report

    monkeypatch.setattr(api_app, "run_growth_intelligence", fake_live_run)
    app = api_app.create_app(build_demo_runtime())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/growth-intelligence/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_source_mode"] == "live"
    assert payload["revenue"]["source"] == "stripe"
    assert payload["google_ads"]["source"] == "google_ads"
    assert payload["meta_ads"]["source"] == "meta_ads"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        live_ledger = (await client.get("/api/v1/audit-log?mode=live")).json()
        demo_ledger = (await client.get("/api/v1/audit-log?mode=demo")).json()

    assert live_ledger["mode"] == "live"
    assert live_ledger["total"] == 1
    assert live_ledger["entries"][0]["action"].startswith("Measured Growth Score ")
    assert "growth baseline" in live_ledger["entries"][0]["reason"]
    assert demo_ledger["mode"] == "demo"
    assert all(entry["mode"] == "demo" for entry in demo_ledger["entries"])


@pytest.mark.asyncio
async def test_live_dashboard_names_an_expired_google_ads_connection(monkeypatch) -> None:
    async def fake_live_run(_settings: Settings):
        raise GrowthSourceError(
            [
                (
                    "google_ads",
                    IntegrationAPIError("google_ads", 401, "ACCESS_TOKEN_EXPIRED"),
                )
            ]
        )

    monkeypatch.setattr(api_app, "run_growth_intelligence", fake_live_run)
    app = api_app.create_app(build_demo_runtime())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/growth-intelligence/live")
        ledger = (await client.get("/api/v1/audit-log?mode=live")).json()

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Google Ads sign-in has expired or lost permission. "
        "Update the Google Ads OAuth settings on the GrowthOS server "
        "(a refresh token prevents hourly expiry), then load Live data again."
    )
    assert ledger["total"] == 0
    assert ledger["entries"] == []
