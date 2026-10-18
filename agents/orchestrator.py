"""LangGraph Growth Orchestrator with an explicit human approval gate."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import NotRequired, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from agents.ads_agent import AdsAgent, CampaignAnalysis
from agents.founder_intent import (
    AcknowledgementNarrative,
    analyze_founder_message,
    deterministic_acknowledgement,
)
from agents.gemini_growth_advisor import StructuredGenerator
from agents.geo_agent import AIVisibilityObservation, GEOAgent, GEOAnalysis
from backend.models.actions import CampaignDraft, ProposedAction
from backend.models.approval import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalStatus,
)
from backend.models.audit_log import AgentActionStatus
from backend.models.founder_experience import (
    FounderMetricSnapshot,
    Phase3Handoff,
)
from backend.models.founder_narrative import (
    ExpectedImpact,
    FounderAgentResponse,
    NarrativeProblem,
)
from backend.services.approval import ApprovalService
from backend.services.audit_log import AuditLogService
from backend.services.campaign_execution import CampaignExecutionService
from backend.services.growth_intelligence import (
    DataSourceMode,
    GrowthIntelligenceReport,
    GrowthIntelligenceRequest,
    GrowthIntelligenceService,
)
from integrations.google_ads import GoogleAdsCampaignCreateResult

logger = logging.getLogger(__name__)


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


class GrowthDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    growth_score: Decimal = Field(ge=0, le=100)
    weakest_component: str
    evidence: list[str] = Field(min_length=1)


class GrowthActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str
    recommendations: list[str] = Field(min_length=1, max_length=5)
    proposed_action: ProposedAction


class MeasurementRecord(BaseModel):
    """Baseline and post-action measurement intent; no immediate lift is fabricated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    baseline_growth_score: Decimal
    baseline_roas: Decimal | None = None
    measurement_window_days: int = 7
    note: str


class TraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node: str
    attempt: int = Field(ge=1)
    outcome: str
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OrchestratorRequest(BaseModel):
    """All inputs required for one bounded Phase 2 workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    growth_request: GrowthIntelligenceRequest
    founder_message: str = Field(min_length=1, max_length=500)
    data_source_mode: DataSourceMode = DataSourceMode.DEMO
    visibility_observations: list[AIVisibilityObservation] = Field(min_length=1)
    campaign_budget: Decimal = Field(default=Decimal("5000.00"), gt=0)


class WorkflowRun(BaseModel):
    """Serializable workflow state exposed by the Phase 2 API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: WorkflowStatus
    report: GrowthIntelligenceReport
    diagnosis: GrowthDiagnosis
    campaign_analysis: CampaignAnalysis
    campaign_draft: CampaignDraft
    geo_analysis: GEOAnalysis
    plan: GrowthActionPlan
    narrative: FounderAgentResponse
    approval: ApprovalRecord
    execution: GoogleAdsCampaignCreateResult | None = None
    measurement: MeasurementRecord
    phase3_handoff: Phase3Handoff
    trace: list[TraceEntry]
    started_at: datetime
    updated_at: datetime


class OrchestratorState(TypedDict):
    run_id: str
    request: OrchestratorRequest
    attempt: int
    trace: list[TraceEntry]
    started_at: datetime
    status: WorkflowStatus
    report: NotRequired[GrowthIntelligenceReport]
    diagnosis: NotRequired[GrowthDiagnosis]
    campaign_analysis: NotRequired[CampaignAnalysis]
    campaign_draft: NotRequired[CampaignDraft]
    geo_analysis: NotRequired[GEOAnalysis]
    plan: NotRequired[GrowthActionPlan]
    narrative: NotRequired[FounderAgentResponse]
    approval: NotRequired[ApprovalRecord]
    execution: NotRequired[GoogleAdsCampaignCreateResult | None]
    measurement: NotRequired[MeasurementRecord]
    phase3_handoff: NotRequired[Phase3Handoff]
    updated_at: NotRequired[datetime]


class GrowthOrchestrator:
    """Run the frozen workflow and resume it only after a human decision."""

    def __init__(
        self,
        *,
        intelligence: GrowthIntelligenceService,
        ads_agent: AdsAgent,
        geo_agent: GEOAgent,
        approvals: ApprovalService,
        audit_log: AuditLogService,
        campaign_execution: CampaignExecutionService,
        run_id_factory: Callable[[], str] | None = None,
        narrator: StructuredGenerator | None = None,
    ) -> None:
        self._narrator = narrator
        self._intelligence = intelligence
        self._ads_agent = ads_agent
        self._geo_agent = geo_agent
        self._approvals = approvals
        self._audit_log = audit_log
        self._campaign_execution = campaign_execution
        self._run_id_factory = run_id_factory or (lambda: str(uuid4()))
        self._runs: dict[str, OrchestratorState] = {}
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(OrchestratorState)
        builder.add_node("collect_data", self._collect_data)
        builder.add_node("analyze", self._analyze)
        builder.add_node("call_agents", self._call_agents)
        builder.add_node("generate_plan", self._generate_plan)
        builder.add_node("approval", self._approval)
        builder.add_node("execute", self._execute)
        builder.add_node("measure", self._measure)
        builder.add_conditional_edges(
            START,
            self._entry_route,
            {"collect_data": "collect_data", "approval": "approval"},
        )
        builder.add_edge("collect_data", "analyze")
        builder.add_edge("analyze", "call_agents")
        builder.add_edge("call_agents", "generate_plan")
        builder.add_edge("generate_plan", "approval")
        builder.add_edge("approval", "execute")
        builder.add_edge("execute", "measure")
        builder.add_edge("measure", END)
        return builder.compile()

    async def start(self, request: OrchestratorRequest) -> WorkflowRun:
        now = datetime.now(UTC)
        state: OrchestratorState = {
            "run_id": self._run_id_factory(),
            "request": request,
            "attempt": 1,
            "trace": [],
            "started_at": now,
            "status": WorkflowStatus.RUNNING,
        }
        result = await self.graph.ainvoke(state)
        self._runs[state["run_id"]] = result
        return self._to_run(result)

    async def resume(self, run_id: str, decision: ApprovalDecision) -> WorkflowRun:
        state = self._get_state(run_id)
        # A stale or unavailable baseline must not reach an approval side effect.
        state["report"].growth_score.require_available()
        approval = state["approval"]
        updated_approval = await self._approvals.decide(approval.approval_id, decision)
        resumed: OrchestratorState = {
            **state,
            "approval": updated_approval,
            "attempt": state["attempt"] + 1,
            "status": WorkflowStatus.RUNNING,
        }
        result = await self.graph.ainvoke(resumed)
        self._runs[run_id] = result
        return self._to_run(result)

    async def get(self, run_id: str) -> WorkflowRun:
        state = self._get_state(run_id)
        if "approval" in state:
            state = {**state, "approval": await self._approvals.get(state["approval"].approval_id)}
            self._runs[run_id] = state
        return self._to_run(state)

    def _get_state(self, run_id: str) -> OrchestratorState:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise KeyError(f"workflow run {run_id} was not found") from error

    @staticmethod
    def _entry_route(state: OrchestratorState) -> str:
        return "approval" if "report" in state else "collect_data"

    async def _collect_data(self, state: OrchestratorState) -> dict:
        request = state["request"]
        report = await self._intelligence.analyze(
            request.growth_request, mode=request.data_source_mode
        )
        return {
            "report": report,
            "trace": self._trace(state, "collect_data", "provider snapshots normalized"),
        }

    async def _analyze(self, state: OrchestratorState) -> dict:
        report = state["report"]
        report.growth_score.require_available()
        weakest = min(report.growth_score.components, key=report.growth_score.components.get)
        evidence = [
            f"Growth Score={report.growth_score.score}/100",
            f"{weakest}={report.growth_score.components[weakest]}/100",
            f"Google Ads ROAS={report.google_ads.roas}",
            f"Meta Ads ROAS={report.meta_ads.roas}",
        ]
        diagnosis = GrowthDiagnosis(
            growth_score=report.growth_score.score,
            weakest_component=weakest,
            evidence=evidence,
        )
        return {
            "diagnosis": diagnosis,
            "trace": self._trace(state, "analyze", f"weakest component: {weakest}"),
        }

    async def _call_agents(self, state: OrchestratorState) -> dict:
        report = state["report"]
        request = state["request"]
        campaign_analysis = self._ads_agent.analyze(report.google_ads, report.meta_ads)
        campaign_draft = self._ads_agent.draft_campaign(
            report.company,
            campaign_analysis,
            budget=request.campaign_budget,
            end_date=report.google_ads.period.end.isoformat(),
        )
        geo_analysis = self._geo_agent.analyze(
            report.company.company_name, request.visibility_observations
        )
        await self._audit_log.record(
            agent="GEO Agent",
            action="Assessed AI search visibility",
            reason=(
                f"Measure {report.company.company_name} visibility and identify the next "
                "search-presence opportunity; current score is "
                f"{geo_analysis.visibility_score:.0f}/100."
            ),
            status=AgentActionStatus.EXECUTED,
            workflow_run_id=state["run_id"],
            idempotency_key=f"{state['run_id']}:geo-analysis",
        )
        return {
            "campaign_analysis": campaign_analysis,
            "campaign_draft": campaign_draft,
            "geo_analysis": geo_analysis,
            "trace": self._trace(state, "call_agents", "Ads and GEO outputs generated"),
        }

    async def _generate_plan(self, state: OrchestratorState) -> dict:
        campaign_analysis = state["campaign_analysis"]
        draft = state["campaign_draft"]
        geo = state["geo_analysis"]
        intent = analyze_founder_message(state["request"].founder_message)
        recommendations = [campaign_analysis.primary_recommendation, geo.action_plan[0].action]
        if intent.leads_with_visibility:
            recommendations.reverse()
        plan = GrowthActionPlan(
            objective="Improve paid acquisition efficiency and AI search visibility",
            recommendations=recommendations,
            proposed_action=ProposedAction.from_campaign_draft(draft),
        )
        primary = next(
            issue
            for issue in campaign_analysis.issues
            if issue.problem == campaign_analysis.primary_problem
            and issue.reason == campaign_analysis.primary_reason
        )
        remaining = [issue for issue in campaign_analysis.issues if issue is not primary]
        ordered_issues = [primary, *remaining]
        primary_finding = (
            f"{primary.platform}: {primary.problem} ({primary.reason.lower()})"
            if not intent.leads_with_visibility
            else f"AI visibility of {geo.visibility_score:.0f}/100 ({geo.brand_status.lower()})"
        )
        acknowledgement = deterministic_acknowledgement(intent, primary_finding)
        if self._narrator is not None:
            acknowledgement = await self._narrate_acknowledgement(
                state["request"].founder_message, intent.topics, primary_finding, acknowledgement
            )
        narrative = FounderAgentResponse(
            founder_message=state["request"].founder_message,
            acknowledgement=acknowledgement,
            top_problems=[
                NarrativeProblem(
                    platform=issue.platform,
                    problem=issue.problem,
                    reason=issue.reason,
                    evidence=issue.evidence,
                )
                for issue in ordered_issues[:3]
            ],
            recommended_actions=plan.recommendations,
            expected_impact=ExpectedImpact(
                expected_roas=draft.expected_kpi.roas,
                budget=draft.total_budget,
                currency=draft.currency,
                disclaimer=(
                    "Expected impact is a planning target and will be measured after seven days."
                ),
            ),
            approval_prompt="Approve this campaign?",
        )
        return {
            "plan": plan,
            "narrative": narrative,
            "trace": self._trace(state, "generate_plan", "one executable action proposed"),
        }

    async def _approval(self, state: OrchestratorState) -> dict:
        if "approval" in state:
            approval = await self._approvals.get(state["approval"].approval_id)
        else:
            approval = await self._approvals.request(
                workflow_run_id=state["run_id"],
                action=state["plan"].proposed_action,
            )
        return {
            "approval": approval,
            "trace": self._trace(state, "approval", f"status: {approval.status}"),
        }

    async def _execute(self, state: OrchestratorState) -> dict:
        approval = state["approval"]
        execution: GoogleAdsCampaignCreateResult | None = state.get("execution")
        if approval.status is ApprovalStatus.APPROVED:
            try:
                execution = await self._campaign_execution.execute(approval.approval_id)
            except Exception as error:
                await self._audit_log.record(
                    agent="Ads Agent",
                    action="Create Google Search campaign",
                    reason=(
                        f"{self._ads_action_reason(state['campaign_analysis'])} Failure: {error}"
                    )[:500],
                    status=AgentActionStatus.FAILED,
                    workflow_run_id=state["run_id"],
                    approval_id=approval.approval_id,
                    idempotency_key=f"{state['run_id']}:campaign-failed",
                )
                return {
                    "execution": None,
                    "status": WorkflowStatus.FAILED,
                    "approval": await self._approvals.get(approval.approval_id),
                    "trace": self._trace(state, "execute", f"failed: {error}"),
                }
            approval = await self._approvals.get(approval.approval_id)
            status = WorkflowStatus.COMPLETED
            outcome = "approved action executed"
            await self._audit_log.record(
                agent="Ads Agent",
                action="Created Google Search campaign",
                reason=self._ads_action_reason(state["campaign_analysis"]),
                status=AgentActionStatus.EXECUTED,
                workflow_run_id=state["run_id"],
                approval_id=approval.approval_id,
                idempotency_key=f"{state['run_id']}:campaign-executed",
            )
        elif approval.status is ApprovalStatus.EXECUTED:
            execution = GoogleAdsCampaignCreateResult.model_validate(approval.execution_result)
            status = WorkflowStatus.COMPLETED
            outcome = "previous execution reused"
            await self._audit_log.record(
                agent="Ads Agent",
                action="Created Google Search campaign",
                reason=self._ads_action_reason(state["campaign_analysis"]),
                status=AgentActionStatus.EXECUTED,
                workflow_run_id=state["run_id"],
                approval_id=approval.approval_id,
                idempotency_key=f"{state['run_id']}:campaign-executed",
            )
        elif approval.status is ApprovalStatus.REJECTED:
            status = WorkflowStatus.REJECTED
            outcome = "rejected; no provider call"
            await self._audit_log.record(
                agent="Ads Agent",
                action="Create Google Search campaign",
                reason=self._ads_action_reason(state["campaign_analysis"]),
                status=AgentActionStatus.REJECTED,
                workflow_run_id=state["run_id"],
                approval_id=approval.approval_id,
                idempotency_key=f"{state['run_id']}:campaign-rejected",
            )
        elif approval.status is ApprovalStatus.FAILED:
            status = WorkflowStatus.FAILED
            outcome = "provider execution failed"
            await self._audit_log.record(
                agent="Ads Agent",
                action="Create Google Search campaign",
                reason=(
                    f"{self._ads_action_reason(state['campaign_analysis'])} "
                    f"Failure: {approval.failure_message or 'provider execution failed'}"
                )[:500],
                status=AgentActionStatus.FAILED,
                workflow_run_id=state["run_id"],
                approval_id=approval.approval_id,
                idempotency_key=f"{state['run_id']}:campaign-failed",
            )
        else:
            status = WorkflowStatus.AWAITING_APPROVAL
            outcome = "blocked pending explicit approval"
            await self._audit_log.record(
                agent="Ads Agent",
                action="Prepared Google Search campaign",
                reason=self._ads_action_reason(state["campaign_analysis"]),
                status=AgentActionStatus.AWAITING_APPROVAL,
                workflow_run_id=state["run_id"],
                approval_id=approval.approval_id,
                idempotency_key=f"{state['run_id']}:campaign-prepared",
            )
        return {
            "approval": approval,
            "execution": execution,
            "status": status,
            "trace": self._trace(state, "execute", outcome),
        }

    async def _measure(self, state: OrchestratorState) -> dict:
        report = state["report"]
        status = state["status"]
        if status is WorkflowStatus.COMPLETED:
            measurement_status = "measurement_scheduled"
            note = "Execution is complete; compare the same metrics after the 7-day window."
        elif status is WorkflowStatus.REJECTED:
            measurement_status = "closed_without_execution"
            note = "The founder rejected the action; baseline remains available for replanning."
        else:
            measurement_status = "baseline_recorded"
            note = "No lift is claimed before an approved action and observation window."
        measurement = MeasurementRecord(
            status=measurement_status,
            baseline_growth_score=report.growth_score.score,
            baseline_roas=report.google_ads.roas,
            note=note,
        )
        total_spend = report.google_ads.spend + report.meta_ads.spend
        marketing_roi = (
            (report.google_ads.conversion_value + report.meta_ads.conversion_value) / total_spend
            if total_spend
            else None
        )
        approval = state["approval"]
        handoff = Phase3Handoff(
            metrics=FounderMetricSnapshot(
                growth_score=report.growth_score.score,
                revenue=report.revenue.revenue,
                marketing_roi=marketing_roi,
                ai_visibility=Decimal(str(state["geo_analysis"].visibility_score)),
                customer_sentiment=None,
                currency=report.revenue.currency,
            ),
            top_recommendation=state["plan"].recommendations[0],
            pending_approval_id=(
                approval.approval_id if approval.status is ApprovalStatus.PENDING else None
            ),
        )
        return {
            "measurement": measurement,
            "phase3_handoff": handoff,
            "updated_at": datetime.now(UTC),
            "trace": self._trace(state, "measure", measurement_status),
        }

    async def _narrate_acknowledgement(
        self,
        founder_message: str,
        topics: tuple[str, ...],
        primary_finding: str,
        fallback: str,
    ) -> str:
        """Let Gemini phrase the reply from grounded facts; fall back on any failure."""

        prompt = (
            "You are a startup growth analyst replying to a founder in one or two sentences. "
            "Answer what the founder asked. Use only these facts; do not add numbers or causes. "
            "If the founder asked about something the facts do not cover, say so plainly.\n\n"
            f"Founder message: {founder_message}\n"
            f"Topics detected: {', '.join(topics) or 'none'}\n"
            f"Main finding from connected data: {primary_finding}\n"
            f"Reference reply: {fallback}"
        )
        try:
            assert self._narrator is not None
            payload = await self._narrator.generate_json(
                prompt=prompt, response_schema=AcknowledgementNarrative.model_json_schema()
            )
            return AcknowledgementNarrative.model_validate(payload).acknowledgement
        except Exception:  # noqa: BLE001 - narration is optional; never block the workflow
            logger.warning("Gemini narration failed; using the deterministic reply", exc_info=True)
            return fallback

    @staticmethod
    def _trace(state: OrchestratorState, node: str, outcome: str) -> list[TraceEntry]:
        return [
            *state.get("trace", []),
            TraceEntry(node=node, attempt=state["attempt"], outcome=outcome),
        ]

    @staticmethod
    def _ads_action_reason(campaign_analysis: CampaignAnalysis) -> str:
        return (
            f"Improve CAC — {campaign_analysis.primary_problem}: {campaign_analysis.primary_reason}"
        )

    @staticmethod
    def _to_run(state: OrchestratorState) -> WorkflowRun:
        return WorkflowRun(
            run_id=state["run_id"],
            status=state["status"],
            report=state["report"],
            diagnosis=state["diagnosis"],
            campaign_analysis=state["campaign_analysis"],
            campaign_draft=state["campaign_draft"],
            geo_analysis=state["geo_analysis"],
            plan=state["plan"],
            narrative=state["narrative"],
            approval=state["approval"],
            execution=state.get("execution"),
            measurement=state["measurement"],
            phase3_handoff=state["phase3_handoff"],
            trace=state["trace"],
            started_at=state["started_at"],
            updated_at=state.get("updated_at", datetime.now(UTC)),
        )
