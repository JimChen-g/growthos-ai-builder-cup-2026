"""Composition root for the offline Phase 2 demo and FastAPI application."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from agents.ads_agent import AdsAgent
from agents.gemini_growth_advisor import StructuredGenerator
from agents.geo_agent import AIVisibilityObservation, GEOAgent
from agents.orchestrator import GrowthOrchestrator, OrchestratorRequest
from backend.config import Settings
from backend.models.connectors import DataMode
from backend.services.approval import ApprovalService
from backend.services.audit_log import AuditLogService
from backend.services.campaign_execution import CampaignExecutionService
from backend.services.growth_intelligence import (
    GrowthIntelligenceService,
    StaticAdsSource,
    StaticRevenueSource,
    load_demo_case,
)
from integrations.google_ads import (
    GoogleAdsCampaignCreateRequest,
    GoogleAdsCampaignCreateResult,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEMO_FOUNDER_MESSAGE = (
    "We are a five-person AI customer support startup at $30k MRR. "
    "CAC is rising and AI visibility is low."
)


def _sequential_id_factory(prefix: str) -> Callable[[], str]:
    sequence = 0

    def next_id() -> str:
        nonlocal sequence
        sequence += 1
        return f"{prefix}-{sequence:04d}"

    return next_id


def _demo_timestamp_factory() -> Callable[[], datetime]:
    """Keep the sample ledger stable so its public testnet proof survives restarts."""

    sequence = 0
    baseline = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

    def next_timestamp() -> datetime:
        nonlocal sequence
        value = baseline + timedelta(seconds=sequence)
        sequence += 1
        return value

    return next_timestamp


class DemoGoogleAdsWriter:
    """Offline write adapter that records calls and returns synthetic resource names."""

    def __init__(self) -> None:
        self.calls: list[GoogleAdsCampaignCreateRequest] = []

    async def create_campaign(
        self, request: GoogleAdsCampaignCreateRequest
    ) -> GoogleAdsCampaignCreateResult:
        self.calls.append(request)
        sequence = len(self.calls)
        return GoogleAdsCampaignCreateResult(
            campaign_resource_name=f"customers/demo/campaigns/{sequence}",
            budget_resource_name=f"customers/demo/campaignBudgets/{sequence}",
            ad_group_resource_name=(
                f"customers/demo/adGroups/{sequence}" if request.has_ad_assets else None
            ),
            ad_resource_name=(
                f"customers/demo/adGroupAds/{sequence}~1" if request.has_ad_assets else None
            ),
            keyword_count=len(request.keywords),
        )


@dataclass(frozen=True)
class Phase2Runtime:
    orchestrator: GrowthOrchestrator
    approvals: ApprovalService
    audit_log: AuditLogService
    demo_request: OrchestratorRequest
    writer: DemoGoogleAdsWriter = field(repr=False)


def build_demo_runtime(
    settings: Settings | None = None,
    *,
    narrator: StructuredGenerator | None = None,
) -> Phase2Runtime:
    """Build the credential-free slowing-growth narrative used for Phase 2."""

    configured = settings or Settings(_env_file=None)
    case = load_demo_case(configured.phase2_demo_fixture_path)
    geo_path = REPOSITORY_ROOT / "data/demo_geo_visibility.json"
    geo_payload = json.loads(geo_path.read_text(encoding="utf-8"))
    observations = TypeAdapter(list[AIVisibilityObservation]).validate_python(
        geo_payload["observations"]
    )

    approvals = ApprovalService(approval_id_factory=_sequential_id_factory("demo-approval"))
    audit_log = AuditLogService(
        mode=DataMode.DEMO,
        entry_id_factory=_sequential_id_factory("demo-audit-entry"),
        timestamp_factory=_demo_timestamp_factory(),
    )
    writer = DemoGoogleAdsWriter()
    intelligence = GrowthIntelligenceService(
        revenue_source=StaticRevenueSource(case.revenue),
        google_ads_source=StaticAdsSource(case.google_ads),
        meta_ads_source=StaticAdsSource(case.meta_ads),
    )
    campaign_execution = CampaignExecutionService(approvals=approvals, google_ads_writer=writer)
    orchestrator = GrowthOrchestrator(
        intelligence=intelligence,
        ads_agent=AdsAgent(),
        geo_agent=GEOAgent(),
        approvals=approvals,
        audit_log=audit_log,
        campaign_execution=campaign_execution,
        run_id_factory=_sequential_id_factory("demo-workflow"),
        narrator=narrator,
    )
    request = OrchestratorRequest(
        growth_request=case.request,
        founder_message=DEMO_FOUNDER_MESSAGE,
        visibility_observations=observations,
        campaign_budget="5000.00",
    )
    return Phase2Runtime(
        orchestrator=orchestrator,
        approvals=approvals,
        audit_log=audit_log,
        demo_request=request,
        writer=writer,
    )
