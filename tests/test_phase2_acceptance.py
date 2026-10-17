from decimal import Decimal

import httpx
import pytest

from backend.api.app import create_app
from backend.models.approval import ApprovalDecision, ApprovalOutcome, ApprovalStatus
from backend.services.phase2_runtime import build_demo_runtime

EXPECTED_INITIAL_TRACE = [
    "collect_data",
    "analyze",
    "call_agents",
    "generate_plan",
    "approval",
    "execute",
    "measure",
]


@pytest.mark.asyncio
async def test_phase2_workflow_waits_then_executes_only_after_approval() -> None:
    runtime = build_demo_runtime()

    pending = await runtime.orchestrator.start(runtime.demo_request)

    assert pending.status == "awaiting_approval"
    assert pending.approval.status is ApprovalStatus.PENDING
    assert [entry.node for entry in pending.trace] == EXPECTED_INITIAL_TRACE
    assert pending.execution is None
    assert runtime.writer.calls == []
    assert pending.narrative.founder_message.startswith(
        "We are a five-person AI customer support startup"
    )
    assert pending.narrative.acknowledgement == (
        "I found the acquisition and AI-visibility constraints behind the slowdown."
    )
    assert pending.narrative.top_problems[0].problem == "CAC increased"
    assert pending.narrative.top_problems[0].reason == "Creative fatigue"
    assert (
        pending.narrative.recommended_actions[0] == "Create a new campaign with refreshed creative"
    )
    assert pending.narrative.expected_impact.expected_roas == Decimal("3.2")
    assert pending.narrative.expected_impact.budget == Decimal("5000")
    assert pending.narrative.approval_prompt == "Approve this campaign?"
    assert pending.campaign_draft.keywords
    assert pending.campaign_draft.headlines
    assert pending.geo_analysis.brand_status == "Underrepresented"
    assert pending.geo_analysis.visibility_score == 40
    assert pending.geo_analysis.competitors
    assert len(pending.geo_analysis.action_plan) == 3
    assert pending.phase3_handoff.metrics.growth_score == pending.report.growth_score.score
    assert pending.phase3_handoff.metrics.customer_sentiment is None

    completed = await runtime.orchestrator.resume(
        pending.run_id,
        ApprovalDecision(
            outcome=ApprovalOutcome.APPROVE,
            actor="founder@example.com",
            reason="Approved acceptance test",
            action_digest=pending.approval.action_digest,
        ),
    )

    assert completed.status == "completed"
    assert completed.approval.status is ApprovalStatus.EXECUTED
    assert completed.execution is not None
    assert len(runtime.writer.calls) == 1
    assert [entry.node for entry in completed.trace[-3:]] == ["approval", "execute", "measure"]
    assert completed.measurement.status == "measurement_scheduled"


@pytest.mark.asyncio
async def test_rejection_never_calls_google_ads_writer() -> None:
    runtime = build_demo_runtime()
    pending = await runtime.orchestrator.start(runtime.demo_request)

    rejected = await runtime.orchestrator.resume(
        pending.run_id,
        ApprovalDecision(
            outcome=ApprovalOutcome.REJECT,
            actor="founder@example.com",
            action_digest=pending.approval.action_digest,
        ),
    )

    assert rejected.status == "rejected"
    assert rejected.approval.status is ApprovalStatus.REJECTED
    assert rejected.execution is None
    assert runtime.writer.calls == []


@pytest.mark.asyncio
async def test_phase2_api_exposes_founder_approval_flow_and_phase3_contract() -> None:
    runtime = build_demo_runtime()
    app = create_app(runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        start = await client.post(
            "/api/v1/workflows/demo",
            json={"founder_message": "Paid growth is slowing."},
        )
        assert start.status_code == 201
        pending = start.json()
        assert pending["narrative"]["founder_message"] == "Paid growth is slowing."
        assert pending["narrative"]["top_problems"][0]["problem"] == "CAC increased"
        assert pending["narrative"]["top_problems"][0]["reason"] == "Creative fatigue"

        approvals = await client.get("/api/v1/approvals", params={"status": "pending"})
        assert approvals.status_code == 200
        assert len(approvals.json()) == 1

        decision = await client.post(
            f"/api/v1/workflows/{pending['run_id']}/decision",
            json={
                "outcome": "approve",
                "actor": "founder-ui",
                "reason": "Reviewed in acceptance test",
                "action_digest": pending["approval"]["action_digest"],
            },
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "completed"

        handoff = await client.get(f"/api/v1/workflows/{pending['run_id']}/phase3-handoff")
        assert handoff.status_code == 200
        assert set(handoff.json()["metrics"]) == {
            "growth_score",
            "revenue",
            "marketing_roi",
            "ai_visibility",
            "customer_sentiment",
            "currency",
        }
