from decimal import Decimal

import pytest

from backend.models.actions import CampaignDraft, ExpectedKPI, ProposedAction
from backend.models.approval import ApprovalDecision, ApprovalOutcome, ApprovalStatus
from backend.services.approval import ApprovalService, StaleApprovalError
from backend.services.campaign_execution import CampaignExecutionService
from backend.services.phase2_runtime import DemoGoogleAdsWriter


def campaign_action(*, budget: Decimal = Decimal("5000")) -> ProposedAction:
    draft = CampaignDraft(
        campaign_name="Acme Search",
        audience="SaaS founders",
        keywords=["ai support"],
        headlines=["AI Support", "Resolve Tickets Faster", "Book a Demo"],
        descriptions=["Resolve support faster", "Automate repetitive tickets today."],
        final_url="https://example.com",
        total_budget=budget,
        duration_days=30,
        expected_kpi=ExpectedKPI(roas=Decimal("3.2")),
    )
    return ProposedAction.from_campaign_draft(draft)


@pytest.mark.asyncio
async def test_budget_change_invalidates_the_presented_approval() -> None:
    approvals = ApprovalService()
    record = await approvals.request(workflow_run_id="run-1", action=campaign_action())
    changed_action = campaign_action(budget=Decimal("6000"))

    with pytest.raises(StaleApprovalError):
        await approvals.decide(
            record.approval_id,
            ApprovalDecision(
                outcome=ApprovalOutcome.APPROVE,
                actor="founder",
                action_digest=changed_action.digest(),
            ),
        )

    assert (await approvals.get(record.approval_id)).status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_campaign_execution_requires_approval_and_is_idempotent() -> None:
    approvals = ApprovalService()
    writer = DemoGoogleAdsWriter()
    executor = CampaignExecutionService(approvals=approvals, google_ads_writer=writer)
    record = await approvals.request(workflow_run_id="run-1", action=campaign_action())

    with pytest.raises(Exception, match="not approved"):
        await executor.execute(record.approval_id)
    assert writer.calls == []

    await approvals.decide(
        record.approval_id,
        ApprovalDecision(
            outcome=ApprovalOutcome.APPROVE,
            actor="founder",
            action_digest=record.action_digest,
        ),
    )
    first = await executor.execute(record.approval_id)
    second = await executor.execute(record.approval_id)

    assert first == second
    assert len(writer.calls) == 1
    assert (await approvals.get(record.approval_id)).status is ApprovalStatus.EXECUTED
