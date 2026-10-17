"""FastAPI application through the Phase 5 fixed competition demo."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agents.orchestrator import WorkflowRun
from backend.api.security import (
    approver_identity,
    ensure_live_allowed,
    install_access_control,
    request_workspace_id,
)
from backend.config import Settings
from backend.models.approval import (
    ApprovalDecision,
    ApprovalOutcome,
    ApprovalRecord,
    ApprovalStatus,
)
from backend.models.audit_anchor import AuditBatchProof, AuditIntegrityStatus
from backend.models.audit_log import AgentActionLedger, AgentActionStatus
from backend.models.competition import (
    CompetitionAnalysisRequest,
    CompetitionDecision,
    CompetitionDecisionRequest,
)
from backend.models.connectors import (
    AccountSelectionRequest,
    ConnectionCenter,
    ConnectionStatus,
    ConnectionTestResult,
    ConnectorCredentialRequest,
    ConnectorProvider,
    DataMode,
    FeedbackCollectionRequest,
    FeedbackCollectionResponse,
    GeoScanReport,
    GeoScanRequest,
    MetaAdsConnectionRequest,
    VertexAIConnectionRequest,
)
from backend.models.demo import DemoScript
from backend.models.feedback import FeedbackAnalysis, FeedbackAnalysisRequest
from backend.models.founder_experience import (
    FounderDailyBriefing,
    FounderExperience,
    GrowthDashboard,
    Phase3Handoff,
)
from backend.models.intelligence_history import IntelligenceHistory, IntelligenceKind
from backend.services.approval import ApprovalError, ApprovalNotFoundError
from backend.services.audit_anchor import (
    build_audit_batch_proof,
    build_audit_integrity_status,
    load_public_anchor_receipt,
)
from backend.services.audit_log import AuditLogService
from backend.services.competition_decision import (
    CompetitionDecisionService,
    CompetitionQuotaExceededError,
    build_competition_decision_service,
)
from backend.services.connector_security import CredentialVaultError, VaultUnavailableError
from backend.services.demo import load_demo_script
from backend.services.founder_experience import (
    FounderExperienceService,
    build_demo_founder_experience,
)
from backend.services.growth_intelligence import (
    GrowthIntelligenceReport,
    GrowthSourceError,
    run_growth_intelligence,
)
from backend.services.intelligence_history import IntelligenceHistoryService
from backend.services.phase2_runtime import (
    DEMO_FOUNDER_MESSAGE,
    REPOSITORY_ROOT,
    Phase2Runtime,
    build_demo_runtime,
)
from backend.services.real_intelligence import (
    RealIntelligenceRuntime,
    UnsupportedConnectionError,
    build_real_intelligence_runtime,
)
from integrations.gemini_structured import build_narration_generator

logger = logging.getLogger(__name__)


class WorkflowDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outcome: ApprovalOutcome
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)
    action_digest: str = Field(min_length=64, max_length=64)


class StartDemoWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    founder_message: str = Field(
        default=DEMO_FOUNDER_MESSAGE,
        min_length=1,
        max_length=500,
    )


def create_app(
    runtime: Phase2Runtime | None = None,
    founder_experience: FounderExperienceService | None = None,
    real_intelligence: RealIntelligenceRuntime | None = None,
    live_audit_log: AuditLogService | None = None,
    demo_script: DemoScript | None = None,
    competition_decisions: CompetitionDecisionService | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Create an application with injectable runtime state for tests and deployment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        narrator = build_narration_generator(app.state.settings)
        if not hasattr(app.state, "runtime"):
            app.state.runtime = build_demo_runtime(narrator=narrator)
        if not hasattr(app.state, "founder_experience"):
            app.state.founder_experience = build_demo_founder_experience(
                app.state.runtime, briefing_generator=narrator
            )
        if not hasattr(app.state, "real_intelligence"):
            app.state.real_intelligence = build_real_intelligence_runtime(app.state.settings)
        if not hasattr(app.state, "live_audit_log"):
            app.state.live_audit_log = AuditLogService(mode=DataMode.LIVE)
        if not hasattr(app.state, "demo_script"):
            app.state.demo_script = load_demo_script(Settings().phase5_demo_fixture_path)
        if not hasattr(app.state, "competition_decisions"):
            app.state.competition_decisions = build_competition_decision_service(
                app.state.settings
            )
        yield

    app = FastAPI(
        title="AI Growth Agent API",
        version="0.6.0",
        description="Approval-gated growth orchestration with a fixed three-minute demo",
        lifespan=lifespan,
    )
    if runtime is not None:
        app.state.runtime = runtime
    if founder_experience is not None:
        app.state.founder_experience = founder_experience
    if real_intelligence is not None:
        app.state.real_intelligence = real_intelligence
    if live_audit_log is not None:
        app.state.live_audit_log = live_audit_log
    if demo_script is not None:
        app.state.demo_script = demo_script
    if competition_decisions is not None:
        app.state.competition_decisions = competition_decisions
    app_settings = settings or Settings()
    app.state.settings = app_settings

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's input and context can contain the whole submitted credential body.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": item["loc"], "type": item["type"], "msg": item["msg"]}
                    for item in error.errors()
                ]
            },
        )

    # Access control first so CORS (added last, outermost) also decorates 401/403 replies.
    install_access_control(app, app_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in app_settings.cors_allow_origins.split(",")
            if origin.strip()
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-Tenant-ID", "Authorization"],
    )

    def active_runtime(request: Request) -> Phase2Runtime:
        return request.app.state.runtime

    def active_founder_experience(request: Request) -> FounderExperienceService:
        if not hasattr(request.app.state, "founder_experience"):
            request.app.state.founder_experience = build_demo_founder_experience(
                active_runtime(request)
            )
        return request.app.state.founder_experience

    def active_real_intelligence(request: Request) -> RealIntelligenceRuntime:
        if not hasattr(request.app.state, "real_intelligence"):
            request.app.state.real_intelligence = build_real_intelligence_runtime(
                request.app.state.settings
            )
        return request.app.state.real_intelligence

    def active_audit_log(request: Request, mode: DataMode) -> AuditLogService:
        if mode is DataMode.DEMO:
            return active_runtime(request).audit_log
        if not hasattr(request.app.state, "live_audit_log"):
            request.app.state.live_audit_log = AuditLogService(mode=DataMode.LIVE)
        return request.app.state.live_audit_log

    def active_demo_script(request: Request) -> DemoScript:
        if not hasattr(request.app.state, "demo_script"):
            request.app.state.demo_script = load_demo_script(Settings().phase5_demo_fixture_path)
        return request.app.state.demo_script

    def active_competition_decisions(request: Request) -> CompetitionDecisionService:
        if not hasattr(request.app.state, "competition_decisions"):
            request.app.state.competition_decisions = build_competition_decision_service(
                request.app.state.settings
            )
        return request.app.state.competition_decisions

    def active_intelligence_history(request: Request, mode: DataMode) -> IntelligenceHistoryService:
        state_name = f"{mode.value}_intelligence_history"
        if not hasattr(request.app.state, state_name):
            selected_settings = active_real_intelligence(request).connections.settings
            path = (
                selected_settings.demo_intelligence_history_path
                if mode is DataMode.DEMO
                else selected_settings.live_intelligence_history_path
            )
            resolved = path if path.is_absolute() else REPOSITORY_ROOT / path
            setattr(
                request.app.state,
                state_name,
                IntelligenceHistoryService(mode, resolved),
            )
        return getattr(request.app.state, state_name)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "phase": "5"}

    @app.get("/api/v1/capabilities")
    async def deployment_capabilities() -> JSONResponse:
        """Expose only the workspace policy needed before client authentication."""

        return JSONResponse(
            {
                "public_demo_only": app_settings.public_demo_only,
                "live_available": not app_settings.public_demo_only,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/api/v1/competition/analyze",
        response_model=CompetitionDecision,
        status_code=status.HTTP_201_CREATED,
    )
    async def competition_analyze(
        request: Request,
        body: CompetitionAnalysisRequest,
    ) -> CompetitionDecision:
        try:
            return await active_competition_decisions(request).analyze(
                request_workspace_id(request),
                body,
            )
        except CompetitionQuotaExceededError as error:
            raise HTTPException(status_code=429, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get(
        "/api/v1/competition/workflows/{workflow_id}",
        response_model=CompetitionDecision,
    )
    async def competition_workflow(
        request: Request,
        workflow_id: Annotated[str, Path(min_length=1, max_length=64)],
    ) -> CompetitionDecision:
        try:
            return await active_competition_decisions(request).get(
                request_workspace_id(request),
                workflow_id,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/v1/competition/workflows/{workflow_id}/decision",
        response_model=CompetitionDecision,
    )
    async def competition_workflow_decision(
        request: Request,
        body: CompetitionDecisionRequest,
        workflow_id: Annotated[str, Path(min_length=1, max_length=64)],
    ) -> CompetitionDecision:
        try:
            return await active_competition_decisions(request).decide(
                request_workspace_id(request),
                workflow_id,
                outcome=body.outcome,
                action_digest=body.action_digest,
                actor=approver_identity(request, request.app.state.settings, "founder"),
                reason=body.reason,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/demo", response_model=DemoScript)
    async def fixed_demo_script(request: Request) -> DemoScript:
        """Return the immutable P0-050 case and P0-051 presentation rundown."""

        return active_demo_script(request)

    @app.post(
        "/api/v1/workflows/demo",
        response_model=WorkflowRun,
        status_code=status.HTTP_201_CREATED,
    )
    async def start_demo(
        request: Request,
        body: StartDemoWorkflowRequest | None = None,
    ) -> WorkflowRun:
        selected = active_runtime(request)
        founder_message = body.founder_message if body else selected.demo_request.founder_message
        workflow_request = selected.demo_request.model_copy(
            update={"founder_message": founder_message}
        )
        return await selected.orchestrator.start(workflow_request)

    @app.get("/api/v1/workflows/{run_id}", response_model=WorkflowRun)
    async def get_workflow(
        request: Request,
        run_id: Annotated[str, Path(min_length=1)],
    ) -> WorkflowRun:
        try:
            return await active_runtime(request).orchestrator.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/workflows/{run_id}/decision", response_model=WorkflowRun)
    async def decide_workflow(
        request: Request,
        body: WorkflowDecisionRequest,
        run_id: Annotated[str, Path(min_length=1)],
    ) -> WorkflowRun:
        decision = ApprovalDecision(
            outcome=body.outcome,
            actor=approver_identity(request, request.app.state.settings, body.actor)[:200],
            reason=body.reason,
            action_digest=body.action_digest,
        )
        try:
            return await active_runtime(request).orchestrator.resume(run_id, decision)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ApprovalNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ApprovalError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/approvals", response_model=list[ApprovalRecord])
    async def list_approvals(
        request: Request,
        approval_status: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    ) -> list[ApprovalRecord]:
        return await active_runtime(request).approvals.list(status=approval_status)

    @app.get("/api/v1/audit-log", response_model=AgentActionLedger)
    async def agent_action_ledger(
        request: Request,
        mode: Annotated[DataMode, Query()] = DataMode.DEMO,
        execution_status: Annotated[AgentActionStatus | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AgentActionLedger:
        """Return the append-only Agent Action Ledger, newest event first."""

        if mode is DataMode.DEMO:
            # Demo owns its deterministic history. Live reads never initialize this workflow.
            await active_founder_experience(request).experience()
        return await active_audit_log(request, mode).list(
            status=execution_status,
            limit=limit,
        )

    @app.get("/api/v1/intelligence-history", response_model=IntelligenceHistory)
    async def intelligence_history(
        request: Request,
        mode: Annotated[DataMode, Query()] = DataMode.DEMO,
        kind: Annotated[IntelligenceKind | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> IntelligenceHistory:
        ledger = await active_audit_log(request, mode).list(limit=500)
        return await active_intelligence_history(request, mode).list_snapshots(
            tenant_id=tenant_id,
            kind=kind,
            limit=limit,
            audit_entries=ledger.entries,
        )

    @app.get("/api/v1/audit-proof", response_model=AuditBatchProof)
    async def audit_batch_proof(
        request: Request,
        mode: Annotated[DataMode, Query()] = DataMode.DEMO,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> AuditBatchProof:
        """Build a local Merkle proof without sending data or a transaction."""

        if mode is DataMode.DEMO:
            await active_founder_experience(request).experience()
        ledger = await active_audit_log(request, mode).list(limit=limit)
        try:
            return build_audit_batch_proof(ledger.entries, mode=mode)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/v1/audit-proof/status", response_model=AuditIntegrityStatus)
    async def audit_proof_status(
        request: Request,
        mode: Annotated[DataMode, Query()] = DataMode.DEMO,
    ) -> AuditIntegrityStatus:
        """Compare a ledger only with the receipt owned by the same workspace."""

        if mode is DataMode.DEMO:
            await active_founder_experience(request).experience()
        entries = await active_audit_log(request, mode).all_entries()
        settings = Settings()
        configured_path = (
            settings.demo_audit_anchor_receipt_path
            if mode is DataMode.DEMO
            else settings.live_audit_anchor_receipt_path
        )
        receipt_path = (
            configured_path if configured_path.is_absolute() else REPOSITORY_ROOT / configured_path
        )
        receipt = load_public_anchor_receipt(receipt_path)
        return build_audit_integrity_status(
            entries,
            mode=mode,
            receipt=receipt,
        )

    @app.get(
        "/api/v1/workflows/{run_id}/phase3-handoff",
        response_model=Phase3Handoff,
    )
    async def phase3_handoff(
        request: Request,
        run_id: Annotated[str, Path(min_length=1)],
    ) -> Phase3Handoff:
        """Expose the stable workflow-to-founder-experience handoff contract."""

        try:
            run = await active_runtime(request).orchestrator.get(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return run.phase3_handoff

    @app.get("/api/v1/founder/briefing", response_model=FounderDailyBriefing)
    async def founder_briefing(request: Request) -> FounderDailyBriefing:
        return await active_founder_experience(request).briefing()

    @app.get("/api/v1/dashboard", response_model=GrowthDashboard)
    async def growth_dashboard(request: Request) -> GrowthDashboard:
        return await active_founder_experience(request).dashboard()

    @app.get("/api/v1/founder/experience", response_model=FounderExperience)
    async def founder_experience_view(request: Request) -> FounderExperience:
        return await active_founder_experience(request).experience()

    @app.post(
        "/api/v1/growth-intelligence/live",
        response_model=GrowthIntelligenceReport,
    )
    async def live_growth_intelligence(
        request: Request,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> GrowthIntelligenceReport:
        """Read Stripe, Google Ads, and Meta Ads only after an explicit Live refresh."""

        try:
            selected_settings = await active_real_intelligence(request).connections.growth_settings(
                tenant_id
            )
            report = await run_growth_intelligence(selected_settings)
            await active_audit_log(request, DataMode.LIVE).record(
                agent="Growth Intelligence Engine",
                action=f"Measured Growth Score {report.growth_score.score}/100",
                reason=(
                    "Created a new growth baseline from connected revenue, "
                    "marketing efficiency, market demand, and execution capability."
                ),
                status=AgentActionStatus.EXECUTED,
            )
            return report
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except GrowthSourceError as error:
            for provider, failure in error.failures:
                logger.warning(
                    "Live refresh: %s failed: %r (detail: %s)",
                    provider,
                    failure,
                    getattr(failure, "detail", None),
                )
            raise HTTPException(status_code=502, detail=str(error)) from error
        except Exception as error:
            logger.exception("Live growth refresh failed unexpectedly")
            raise HTTPException(
                status_code=502,
                detail=(
                    "A connected business source could not be read. "
                    "Check Connections and try again."
                ),
            ) from error

    @app.get("/api/v1/feedback/demo", response_model=FeedbackAnalysis)
    async def demo_feedback(request: Request) -> FeedbackAnalysis:
        return await active_founder_experience(request).demo_feedback()

    @app.post("/api/v1/feedback/analyze", response_model=FeedbackAnalysis)
    async def analyze_feedback(request: Request, body: FeedbackAnalysisRequest) -> FeedbackAnalysis:
        return active_founder_experience(request).analyze_feedback(body.signals)

    @app.get("/api/v1/connections", response_model=ConnectionCenter)
    async def connection_center(
        request: Request,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> ConnectionCenter:
        return await active_real_intelligence(request).connections.center(tenant_id)

    @app.put(
        "/api/v1/connections/{provider}/credential",
        response_model=ConnectionStatus,
    )
    async def save_connection_credential(
        request: Request,
        body: ConnectorCredentialRequest,
        provider: Annotated[ConnectorProvider, Path()],
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> ConnectionStatus:
        try:
            return await active_real_intelligence(request).connections.save_secret(
                tenant_id, provider, body.secret
            )
        except (VaultUnavailableError, UnsupportedConnectionError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CredentialVaultError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post(
        "/api/v1/connections/{provider}/account",
        response_model=ConnectionStatus,
    )
    async def select_connection_account(
        request: Request,
        body: AccountSelectionRequest,
        provider: Annotated[ConnectorProvider, Path()],
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> ConnectionStatus:
        try:
            return await active_real_intelligence(request).connections.select_account(
                tenant_id, provider, body.account_id
            )
        except UnsupportedConnectionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.put(
        "/api/v1/connections/meta_ads/config",
        response_model=ConnectionStatus,
    )
    async def save_meta_ads_connection(
        request: Request,
        body: MetaAdsConnectionRequest,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> ConnectionStatus:
        try:
            return await active_real_intelligence(request).connections.save_meta_connection(
                tenant_id, body
            )
        except VaultUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CredentialVaultError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.put(
        "/api/v1/connections/gemini_vertex/config",
        response_model=ConnectionStatus,
    )
    async def save_vertex_ai_connection(
        request: Request,
        body: VertexAIConnectionRequest,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> ConnectionStatus:
        try:
            return await active_real_intelligence(request).connections.save_vertex_connection(
                tenant_id, body
            )
        except VaultUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CredentialVaultError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post(
        "/api/v1/connections/{provider}/test",
        response_model=ConnectionTestResult,
    )
    async def test_connection(
        request: Request,
        provider: Annotated[ConnectorProvider, Path()],
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> ConnectionTestResult:
        selected = active_real_intelligence(request)
        if provider in {
            ConnectorProvider.OPENAI,
            ConnectorProvider.GEMINI,
            ConnectorProvider.GEMINI_VERTEX,
            ConnectorProvider.PERPLEXITY,
        }:
            return await selected.geo.connection_test(tenant_id, provider)
        if provider in {ConnectorProvider.X, ConnectorProvider.REDDIT}:
            return await selected.feedback.connection_test(tenant_id, provider)
        if provider is ConnectorProvider.META_ADS:
            return await selected.connections.meta_connection_test(tenant_id)
        center = await selected.connections.center(tenant_id)
        item = next(entry for entry in center.connections if entry.provider is provider)
        return ConnectionTestResult(
            provider=provider,
            configured=item.configured,
            checked_live=False,
            success=item.configured,
            message=(
                "Configuration is complete; no billable live request was made."
                if item.configured
                else "The required account configuration is incomplete."
            ),
        )

    @app.post("/api/v1/geo/scans", response_model=GeoScanReport)
    async def run_geo_scan(
        request: Request,
        body: GeoScanRequest,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> GeoScanReport:
        if body.mode is DataMode.LIVE:
            ensure_live_allowed(request.app.state.settings)
        report = await active_real_intelligence(request).geo.scan(tenant_id, body)
        related_action_ids: list[str] = []
        if body.mode is DataMode.LIVE:
            entry = await active_audit_log(request, DataMode.LIVE).record(
                agent="GEO Agent",
                action="Assessed live AI search visibility",
                reason=f"Measured {body.brand} across the selected connected AI sources.",
                status=AgentActionStatus.EXECUTED,
            )
            related_action_ids.append(entry.entry_id)
        await active_intelligence_history(request, body.mode).record_geo(
            report,
            related_action_ids=related_action_ids,
        )
        return report

    @app.post(
        "/api/v1/feedback/collect",
        response_model=FeedbackCollectionResponse,
    )
    async def collect_feedback(
        request: Request,
        body: FeedbackCollectionRequest,
        tenant_id: Annotated[
            str,
            Header(
                alias="X-Tenant-ID",
                min_length=1,
                max_length=64,
                pattern=r"^[a-z0-9][a-z0-9_-]*$",
            ),
        ] = "local-demo",
    ) -> FeedbackCollectionResponse:
        if body.mode is DataMode.LIVE:
            ensure_live_allowed(request.app.state.settings)
        response = await active_real_intelligence(request).feedback.collect(tenant_id, body)
        related_action_ids: list[str] = []
        if body.mode is DataMode.LIVE:
            entry = await active_audit_log(request, DataMode.LIVE).record(
                agent="Feedback Agent",
                action="Collected live customer feedback",
                reason=f"Read current public Reddit and X signals for {body.brand}.",
                status=AgentActionStatus.EXECUTED,
            )
            related_action_ids.append(entry.entry_id)
        await active_intelligence_history(request, body.mode).record_feedback(
            response,
            related_action_ids=related_action_ids,
        )
        return response

    return app


app = create_app()
