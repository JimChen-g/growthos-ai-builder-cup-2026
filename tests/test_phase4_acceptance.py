from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.api.app import create_app
from backend.models.approval import ApprovalDecision
from backend.models.audit_log import AgentActionStatus
from backend.models.connectors import DataMode
from backend.services.audit_log import AuditLogService
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


@pytest.mark.asyncio
async def test_audit_log_is_append_only_idempotent_and_newest_first() -> None:
    audit_log = AuditLogService()
    earlier = datetime(2026, 8, 15, 8, tzinfo=UTC)
    later = earlier + timedelta(minutes=5)

    first = await audit_log.record(
        agent="Ads Agent",
        action="Prepared campaign",
        reason="Improve CAC",
        status=AgentActionStatus.AWAITING_APPROVAL,
        timestamp=earlier,
        idempotency_key="run-1:prepared",
    )
    duplicate = await audit_log.record(
        agent="Ads Agent",
        action="This duplicate must not be appended",
        reason="Repeated workflow delivery",
        status=AgentActionStatus.FAILED,
        timestamp=later,
        idempotency_key="run-1:prepared",
    )
    executed = await audit_log.record(
        agent="GEO Agent",
        action="Assessed AI search visibility",
        reason="Measure search presence",
        status=AgentActionStatus.EXECUTED,
        timestamp=later,
    )

    ledger = await audit_log.list()
    filtered = await audit_log.list(status=AgentActionStatus.EXECUTED, limit=1)

    assert duplicate == first
    assert ledger.total == 2
    assert [entry.entry_id for entry in ledger.entries] == [executed.entry_id, first.entry_id]
    assert filtered.total == 1
    assert filtered.entries == [executed]


@pytest.mark.asyncio
async def test_phase4_api_exposes_grounded_agent_action_ledger() -> None:
    runtime = build_demo_runtime()
    founder_experience = build_demo_founder_experience(runtime)
    app = create_app(runtime, founder_experience)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        dashboard = await client.get("/api/v1/dashboard")
        response = await client.get("/api/v1/audit-log?limit=10")
        executed_response = await client.get("/api/v1/audit-log?status=executed&limit=2")
        health = await client.get("/health")

    assert dashboard.status_code == 200
    assert response.status_code == 200
    assert health.json()["status"] == "ok"
    payload = response.json()
    assert payload["total"] == 3
    assert {entry["agent"] for entry in payload["entries"]} == {
        "Ads Agent",
        "Feedback Agent",
        "GEO Agent",
    }
    assert all(
        {"entry_id", "agent", "action", "reason", "timestamp", "status"} <= entry.keys()
        for entry in payload["entries"]
    )
    ads_entry = next(entry for entry in payload["entries"] if entry["agent"] == "Ads Agent")
    assert ads_entry["status"] == "awaiting_approval"
    assert ads_entry["workflow_run_id"] is not None
    assert "Improve CAC" in ads_entry["reason"]
    assert executed_response.status_code == 200
    assert executed_response.json()["total"] == 2
    assert all(entry["status"] == "executed" for entry in executed_response.json()["entries"])


@pytest.mark.asyncio
async def test_demo_and_live_ledgers_are_independent() -> None:
    runtime = build_demo_runtime()
    founder_experience = build_demo_founder_experience(runtime)
    live_audit_log = AuditLogService(mode=DataMode.LIVE)
    app = create_app(runtime, founder_experience, live_audit_log=live_audit_log)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        live_before = (await client.get("/api/v1/audit-log?mode=live")).json()
        demo = (await client.get("/api/v1/audit-log?mode=demo")).json()

        await live_audit_log.record(
            agent="GEO Agent",
            action="Assessed live AI search visibility",
            reason="Founder requested a connected-source scan.",
            status=AgentActionStatus.EXECUTED,
        )

        live_after = (await client.get("/api/v1/audit-log?mode=live")).json()
        demo_after = (await client.get("/api/v1/audit-log?mode=demo")).json()

    assert live_before == {"mode": "live", "entries": [], "total": 0}
    assert demo["mode"] == "demo"
    assert demo["total"] == 3
    assert live_after["mode"] == "live"
    assert live_after["total"] == 1
    assert live_after["entries"][0]["mode"] == "live"
    assert demo_after["total"] == 3
    assert all(entry["mode"] == "demo" for entry in demo_after["entries"])


@pytest.mark.asyncio
async def test_approved_campaign_execution_appends_a_linked_executed_event() -> None:
    runtime = build_demo_runtime()
    founder_experience = build_demo_founder_experience(runtime)
    app = create_app(runtime, founder_experience)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        dashboard = (await client.get("/api/v1/dashboard")).json()
        ads_recommendation = next(
            item for item in dashboard["recommendations"] if item["source_agent"] == "Ads Agent"
        )
        run_id = ads_recommendation["workflow_run_id"]
        workflow = (await client.get(f"/api/v1/workflows/{run_id}")).json()
        decision = await client.post(
            f"/api/v1/workflows/{run_id}/decision",
            json={
                "outcome": "approve",
                "actor": "phase4-acceptance",
                "reason": "Approved for the Phase 4 acceptance path",
                "action_digest": workflow["approval"]["action_digest"],
            },
        )
        ledger = (await client.get("/api/v1/audit-log")).json()

    assert decision.status_code == 200
    assert decision.json()["status"] == "completed"
    executed = next(
        entry for entry in ledger["entries"] if entry["action"] == "Created Google Search campaign"
    )
    assert executed["agent"] == "Ads Agent"
    assert executed["status"] == "executed"
    assert executed["workflow_run_id"] == run_id
    assert executed["approval_id"] == workflow["approval"]["approval_id"]
    assert len(runtime.writer.calls) == 1


@pytest.mark.asyncio
async def test_rejected_campaign_appends_rejected_event_without_provider_write() -> None:
    runtime = build_demo_runtime()
    founder_experience = build_demo_founder_experience(runtime)
    app = create_app(runtime, founder_experience)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        dashboard = (await client.get("/api/v1/dashboard")).json()
        ads_recommendation = next(
            item for item in dashboard["recommendations"] if item["source_agent"] == "Ads Agent"
        )
        run_id = ads_recommendation["workflow_run_id"]
        workflow = (await client.get(f"/api/v1/workflows/{run_id}")).json()
        decision = await client.post(
            f"/api/v1/workflows/{run_id}/decision",
            json={
                "outcome": "reject",
                "actor": "phase4-acceptance",
                "reason": "Keep the current campaign unchanged",
                "action_digest": workflow["approval"]["action_digest"],
            },
        )
        ledger = (await client.get("/api/v1/audit-log?status=rejected")).json()

    assert decision.status_code == 200
    assert decision.json()["status"] == "rejected"
    assert ledger["total"] == 1
    assert ledger["entries"][0]["action"] == "Create Google Search campaign"
    assert ledger["entries"][0]["approval_id"] == workflow["approval"]["approval_id"]
    assert runtime.writer.calls == []


@pytest.mark.asyncio
async def test_failed_campaign_execution_appends_failed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = build_demo_runtime()

    async def fail_campaign_creation(_request) -> None:
        raise RuntimeError("simulated provider failure")

    monkeypatch.setattr(runtime.writer, "create_campaign", fail_campaign_creation)
    pending = await runtime.orchestrator.start(runtime.demo_request)
    failed = await runtime.orchestrator.resume(
        pending.run_id,
        ApprovalDecision(
            outcome="approve",
            actor="phase4-acceptance",
            action_digest=pending.approval.action_digest,
        ),
    )
    ledger = await runtime.audit_log.list(status=AgentActionStatus.FAILED)

    assert failed.status == "failed"
    assert ledger.total == 1
    assert ledger.entries[0].workflow_run_id == pending.run_id
    assert ledger.entries[0].approval_id == pending.approval.approval_id
    assert "simulated provider failure" in ledger.entries[0].reason
