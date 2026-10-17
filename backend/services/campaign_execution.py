"""Approval-gated bridge from campaign drafts to the Google Ads write adapter."""

from typing import Protocol

from backend.models.actions import ActionKind, CampaignDraft
from backend.models.approval import ApprovalStatus
from backend.services.approval import ApprovalService
from integrations.google_ads import (
    GoogleAdsCampaignCreateRequest,
    GoogleAdsCampaignCreateResult,
)


class GoogleCampaignWriter(Protocol):
    async def create_campaign(
        self, request: GoogleAdsCampaignCreateRequest
    ) -> GoogleAdsCampaignCreateResult: ...


class CampaignExecutionService:
    """Execute exactly the approved payload once and record the provider result."""

    def __init__(
        self, *, approvals: ApprovalService, google_ads_writer: GoogleCampaignWriter
    ) -> None:
        self._approvals = approvals
        self._writer = google_ads_writer

    async def execute(self, approval_id: str) -> GoogleAdsCampaignCreateResult:
        existing = await self._approvals.get(approval_id)
        if existing.status is ApprovalStatus.EXECUTED and existing.execution_result:
            return GoogleAdsCampaignCreateResult.model_validate(existing.execution_result)

        claimed = await self._approvals.claim_for_execution(approval_id)
        if claimed.action.kind is not ActionKind.CREATE_GOOGLE_CAMPAIGN:
            await self._approvals.mark_failed(
                approval_id, message=f"unsupported action kind: {claimed.action.kind}"
            )
            raise ValueError(f"unsupported action kind: {claimed.action.kind}")

        draft = CampaignDraft.model_validate(claimed.action.payload)
        # Send every approved asset, so the provider receives exactly what was approved.
        request = GoogleAdsCampaignCreateRequest(
            campaign_name=draft.campaign_name,
            daily_budget=draft.daily_budget,
            currency=draft.currency,
            keywords=draft.keywords,
            headlines=draft.headlines,
            descriptions=draft.descriptions,
            final_url=str(draft.final_url),
        )
        try:
            result = await self._writer.create_campaign(request)
        except Exception as error:
            await self._approvals.mark_failed(approval_id, message=str(error))
            raise

        await self._approvals.mark_executed(approval_id, result=result.model_dump(mode="json"))
        return result
