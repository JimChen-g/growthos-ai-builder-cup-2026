"""Phase 3 composition service for briefing, dashboard, and feedback intelligence."""

import asyncio
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agents.feedback_agent import FeedbackAgent
from agents.founder_briefing_agent import (
    BriefingNarrativeContext,
    FounderBriefingAgent,
    StructuredBriefingGenerator,
)
from agents.geo_agent import AIVisibilityObservation
from agents.orchestrator import WorkflowRun
from backend.models.audit_log import AgentActionStatus
from backend.models.connectors import DataMode, DataProvenance
from backend.models.feedback import FeedbackAnalysis, FeedbackSignal
from backend.models.founder_experience import (
    BriefingSignal,
    DashboardMetric,
    DashboardRecommendation,
    FounderDailyBriefing,
    FounderExperience,
    GrowthDashboard,
    MetricTone,
)
from backend.services.phase2_runtime import REPOSITORY_ROOT, Phase2Runtime


class Phase3DemoFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    visibility_observations: list[AIVisibilityObservation] = Field(min_length=1)
    feedback_signals: list[FeedbackSignal] = Field(min_length=1)


class FounderExperienceService:
    """Project Phase 1/2 outputs into the three bounded Phase 3 surfaces."""

    def __init__(
        self,
        *,
        phase2_runtime: Phase2Runtime,
        fixture: Phase3DemoFixture,
        feedback_agent: FeedbackAgent | None = None,
        briefing_agent: FounderBriefingAgent | None = None,
    ) -> None:
        self._phase2_runtime = phase2_runtime
        self._fixture = fixture
        self._feedback_agent = feedback_agent or FeedbackAgent()
        self._briefing_agent = briefing_agent or FounderBriefingAgent()
        self._workflow: WorkflowRun | None = None
        self._feedback: FeedbackAnalysis | None = None
        self._lock = asyncio.Lock()

    async def briefing(self) -> FounderDailyBriefing:
        run, feedback = await self._inputs()
        return await self._build_briefing(run, feedback)

    async def dashboard(self) -> GrowthDashboard:
        run, feedback = await self._inputs()
        return self._build_dashboard(run, feedback)

    async def experience(self) -> FounderExperience:
        run, feedback = await self._inputs()
        return FounderExperience(
            briefing=await self._build_briefing(run, feedback),
            dashboard=self._build_dashboard(run, feedback),
        )

    async def demo_feedback(self) -> FeedbackAnalysis:
        _, feedback = await self._inputs()
        return feedback

    def analyze_feedback(self, signals: list[FeedbackSignal]) -> FeedbackAnalysis:
        return self._feedback_agent.analyze(signals)

    async def _inputs(self) -> tuple[WorkflowRun, FeedbackAnalysis]:
        if self._workflow is None or self._feedback is None:
            async with self._lock:
                if self._workflow is None:
                    request = self._phase2_runtime.demo_request.model_copy(
                        update={"visibility_observations": self._fixture.visibility_observations}
                    )
                    self._workflow = await self._phase2_runtime.orchestrator.start(request)
                if self._feedback is None:
                    self._feedback = self._feedback_agent.analyze(self._fixture.feedback_signals)
                    await self._phase2_runtime.audit_log.record(
                        agent="Feedback Agent",
                        action="Analyzed Reddit and X customer feedback",
                        reason=(
                            f"Surface customer intelligence — main problem: "
                            f"{self._feedback.main_problem}."
                        ),
                        status=AgentActionStatus.EXECUTED,
                        workflow_run_id=self._workflow.run_id,
                        idempotency_key=f"{self._workflow.run_id}:feedback-analysis",
                    )
        # Re-read the run so an approval made in Agent Actions shows up on the dashboard
        # and briefing instead of the snapshot taken when the workflow started.
        current = await self._phase2_runtime.orchestrator.get(self._workflow.run_id)
        return current, self._feedback

    async def _build_briefing(
        self, run: WorkflowRun, feedback: FeedbackAnalysis
    ) -> FounderDailyBriefing:
        report = run.report
        primary_issue = next(
            issue
            for issue in run.campaign_analysis.issues
            if issue.problem == run.campaign_analysis.primary_problem
            and issue.reason == run.campaign_analysis.primary_reason
        )
        main_risk = f"{primary_issue.platform} {primary_issue.problem}"
        risk_detail = " · ".join(primary_issue.evidence[2:4])
        top_recommendation = "Improve AI Search Presence"
        context = BriefingNarrativeContext(
            company_name=report.company.company_name,
            growth_score=f"{report.growth_score.score}/100",
            growth_band=report.growth_score.band.value,
            revenue_change=self._signed(report.revenue.growth_rate_pct or Decimal("0")),
            revenue_is_period_total=True,
            google_roas_change=self._signed(report.google_ads.trend_pct or Decimal("0")),
            ai_visibility_change="No prior comparison is supplied",
            risk=main_risk,
            recommendation=top_recommendation,
        )
        narrative = await self._briefing_agent.narrate(context)
        return FounderDailyBriefing(
            salutation="Good Morning, Founder",
            company_name=report.company.company_name,
            growth_health=report.growth_score.score,
            health_band=report.growth_score.band.value,
            executive_summary=narrative.executive_summary,
            yesterday=[
                BriefingSignal(
                    key="revenue",
                    label="Monthly recurring revenue (MRR)",
                    current_value=(
                        f"${report.revenue.mrr:,.0f}" if report.revenue.mrr is not None else "—"
                    ),
                    change_pct=None,
                    context="Month-end subscription snapshot; no prior MRR comparison",
                    provenance=self._demo_source(
                        "Stripe fixture",
                        "Synthetic Phase 1 revenue snapshot; Stripe is not called.",
                    ),
                ),
                BriefingSignal(
                    key="google_ads_roas",
                    label="Google Ads ROAS",
                    current_value=f"{report.google_ads.roas or Decimal('0')}x",
                    change_pct=report.google_ads.trend_pct,
                    context="vs. prior reporting period",
                    provenance=self._demo_source(
                        "Google Ads fixture",
                        "Synthetic campaign performance; Google Ads is not called.",
                    ),
                ),
                BriefingSignal(
                    key="ai_visibility",
                    label="AI Visibility",
                    current_value=f"{run.geo_analysis.visibility_score:.0f}/100",
                    change_pct=None,
                    context=(
                        f"Brand appeared in {run.geo_analysis.brand_mentions} of "
                        f"{run.geo_analysis.prompt_count} tracked prompts"
                    ),
                    provenance=self._demo_source(
                        "GEO Agent fixture",
                        "Deterministic visibility observations; no AI provider is called.",
                    ),
                ),
            ],
            main_risk=main_risk,
            risk_detail=risk_detail,
            top_recommendation=top_recommendation,
            recommendation_reason=narrative.recommendation_reason,
            provenance=self._demo_source(
                "Competition demo workspace",
                "Synthetic Phase 1 and Phase 2 fixtures; no external provider is called.",
            ),
            pending_approval_id=run.phase3_handoff.pending_approval_id,
        )

    def _build_dashboard(self, run: WorkflowRun, feedback: FeedbackAnalysis) -> GrowthDashboard:
        report = run.report
        marketing_roi = run.phase3_handoff.metrics.marketing_roi or Decimal("0")
        metrics = [
            DashboardMetric(
                key="growth_score",
                label="Growth Score",
                value=report.growth_score.score,
                formatted_value=f"{report.growth_score.score:.0f} / 100",
                trend=report.growth_score.band.value.title(),
                source="Growth Score Engine V1",
                provenance=self._demo_source(
                    "Growth Score Engine fixture",
                    "Calculated from the complete synthetic company snapshot.",
                ),
                tone=MetricTone.POSITIVE,
            ),
            DashboardMetric(
                key="revenue",
                label="Monthly recurring revenue (MRR)",
                value=report.revenue.mrr,
                formatted_value=(
                    f"${report.revenue.mrr:,.0f}" if report.revenue.mrr is not None else "—"
                ),
                trend="Month-end subscription snapshot; no prior MRR comparison",
                source="Stripe",
                provenance=self._demo_source(
                    "Stripe fixture",
                    "Synthetic MRR and revenue trend; Stripe is not called.",
                ),
                tone=MetricTone.POSITIVE,
            ),
            DashboardMetric(
                key="marketing_roi",
                label="Combined ad ROAS",
                value=marketing_roi,
                formatted_value=f"{marketing_roi:.1f}x ROAS",
                trend=f"Google {report.google_ads.roas}x · Meta {report.meta_ads.roas}x",
                source="Google Ads + Meta Ads",
                provenance=self._demo_source(
                    "Ads fixtures",
                    "Synthetic Google Ads and Meta Ads performance snapshots.",
                ),
                tone=(MetricTone.POSITIVE if marketing_roi >= 3 else MetricTone.ATTENTION),
            ),
            DashboardMetric(
                key="ai_visibility",
                label="AI Visibility",
                value=Decimal(str(run.geo_analysis.visibility_score)),
                formatted_value=f"{run.geo_analysis.visibility_score:.0f} / 100",
                trend=(
                    f"Brand appeared in {run.geo_analysis.brand_mentions} of "
                    f"{run.geo_analysis.prompt_count} tracked prompts"
                ),
                source="GEO Agent",
                provenance=self._demo_source(
                    "GEO Agent fixture",
                    "Deterministic brand-visibility observations; no AI provider is called.",
                ),
                tone=(
                    MetricTone.POSITIVE
                    if run.geo_analysis.visibility_score >= 60
                    else MetricTone.WATCH
                ),
            ),
            DashboardMetric(
                key="customer_sentiment",
                label="Customer Sentiment",
                value=feedback.sentiment_score,
                formatted_value=f"{feedback.sentiment_score:.0f} / 100",
                trend=f"{feedback.positive_pct:.0f}% positive across Reddit + X",
                source="Feedback Agent",
                provenance=self._demo_source(
                    "Reddit + X fixtures",
                    "Synthetic customer posts analyzed by the real Feedback Agent rules.",
                ),
                tone=(MetricTone.POSITIVE if feedback.sentiment_score >= 65 else MetricTone.WATCH),
            ),
        ]
        return GrowthDashboard(
            company_name=report.company.company_name,
            metrics=metrics,
            recommendations=[
                DashboardRecommendation(
                    priority=1,
                    source_agent="GEO Agent",
                    action="Improve AI Search Presence",
                    rationale=run.geo_analysis.action_plan[0].action,
                    provenance=self._demo_source(
                        "GEO Agent fixture",
                        "Recommendation generated from deterministic demo observations.",
                    ),
                ),
                DashboardRecommendation(
                    priority=2,
                    source_agent="Ads Agent",
                    action=run.campaign_analysis.primary_recommendation,
                    rationale=(
                        f"{run.campaign_analysis.primary_problem}: "
                        f"{run.campaign_analysis.primary_reason}"
                    ),
                    provenance=self._demo_source(
                        "Phase 2 Ads Agent demo",
                        "Synthetic recommendation with a simulated, approval-gated write.",
                    ),
                    workflow_run_id=run.run_id,
                    approval_id=run.approval.approval_id,
                    approval_status=run.approval.status.value,
                ),
                DashboardRecommendation(
                    priority=3,
                    source_agent="Feedback Agent",
                    action=feedback.recommendation,
                    rationale=feedback.main_problem,
                    provenance=self._demo_source(
                        "Reddit + X fixtures",
                        "Recommendation generated from synthetic customer feedback.",
                    ),
                ),
            ],
            customer_intelligence=feedback,
            customer_intelligence_provenance=self._demo_source(
                "Reddit + X fixtures",
                "Synthetic customer posts analyzed by the real Feedback Agent rules.",
            ),
            provenance=self._demo_source(
                "Competition demo workspace",
                "Complete synthetic snapshot spanning Phase 1, Phase 2, GEO, and Feedback Agent.",
            ),
        )

    @staticmethod
    def _signed(value: Decimal) -> str:
        return f"{value:+.0f}%"

    @staticmethod
    def _demo_source(label: str, detail: str) -> DataProvenance:
        return DataProvenance(mode=DataMode.DEMO, label=label, detail=detail)


def load_phase3_fixture(path: Path) -> Phase3DemoFixture:
    resolved = path if path.is_absolute() else REPOSITORY_ROOT / path
    return Phase3DemoFixture.model_validate_json(resolved.read_text(encoding="utf-8"))


def build_demo_founder_experience(
    phase2_runtime: Phase2Runtime,
    fixture_path: Path = Path("data/demo_phase3_founder_experience.json"),
    *,
    briefing_generator: StructuredBriefingGenerator | None = None,
) -> FounderExperienceService:
    return FounderExperienceService(
        phase2_runtime=phase2_runtime,
        fixture=load_phase3_fixture(fixture_path),
        briefing_agent=FounderBriefingAgent(briefing_generator),
    )
