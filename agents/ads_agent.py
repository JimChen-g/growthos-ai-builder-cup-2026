"""Deterministic, evidence-backed Ads Agent for Phase 2."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.actions import CampaignDraft, ExpectedKPI
from backend.models.company import CompanyProfile
from integrations.models import AdsSnapshot


def _fit(text: str, limit: int) -> str:
    """Trim ad copy to a platform limit at a word boundary instead of mid-word."""

    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,.;:-|")
    return cut or text[:limit]


def _first_fit(options: list[str], limit: int) -> str:
    """Use the first option that fits whole; otherwise trim the last one."""

    for option in options:
        if len(option) <= limit:
            return option
    return _fit(options[-1], limit)


class CampaignIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str
    problem: str
    reason: str
    recommendation: str
    evidence: list[str] = Field(min_length=1)


class CampaignAnalysis(BaseModel):
    """Ads Agent diagnosis across Google and Meta snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issues: list[CampaignIssue] = Field(min_length=1)
    primary_problem: str
    primary_reason: str
    primary_recommendation: str


class AdsAgent:
    """Analyze paid acquisition and prepare a safe, reviewable draft."""

    def __init__(self, *, target_roas: Decimal = Decimal("3.0")) -> None:
        self._target_roas = target_roas

    def analyze(self, google_ads: AdsSnapshot, meta_ads: AdsSnapshot) -> CampaignAnalysis:
        issues = [self._analyze_snapshot(google_ads), self._analyze_snapshot(meta_ads)]
        primary = min(
            zip((google_ads.efficiency_score, meta_ads.efficiency_score), issues, strict=True),
            key=lambda item: item[0],
        )[1]
        return CampaignAnalysis(
            issues=issues,
            primary_problem=primary.problem,
            primary_reason=primary.reason,
            primary_recommendation=primary.recommendation,
        )

    def draft_campaign(
        self,
        company: CompanyProfile,
        analysis: CampaignAnalysis,
        *,
        budget: Decimal,
        end_date: str,
    ) -> CampaignDraft:
        """Create a Google Search draft without contacting Google Ads."""

        industry = company.industry.strip()
        industry_keyword = industry.casefold()
        keywords = list(
            dict.fromkeys(
                [
                    industry_keyword,
                    f"best {industry_keyword} tools",
                    f"{industry_keyword} software",
                ]
            )
        )
        expected_roas = max(self._target_roas + Decimal("0.2"), Decimal("3.2"))
        # Ad copy is customer-facing: build it from the company profile only, never from
        # the agent's internal diagnosis or recommendation text.
        return CampaignDraft(
            campaign_name=f"{company.company_name} | Growth Search | {end_date}",
            audience=company.target_market,
            keywords=keywords,
            headlines=[
                _fit(f"Try {company.company_name}", 30),
                _first_fit([f"{industry} Built to Scale", f"Better {industry}", industry], 30),
                _fit("Book a Demo Today", 30),
            ],
            descriptions=[
                _fit(f"Built for {company.target_market}.", 90),
                _fit(f"See how {company.company_name} helps your team grow. Start today.", 90),
            ],
            final_url=company.website,
            total_budget=budget,
            duration_days=30,
            currency=company.currency,
            expected_kpi=ExpectedKPI(roas=expected_roas),
        )

    def _analyze_snapshot(self, snapshot: AdsSnapshot) -> CampaignIssue:
        platform = "Google Ads" if snapshot.source == "google_ads" else "Meta Ads"
        evidence = [
            f"Current ROAS: {snapshot.roas if snapshot.roas is not None else 'unavailable'}",
            f"Current CAC: {snapshot.cac if snapshot.cac is not None else 'unavailable'}",
            self._trend_evidence("CAC", snapshot.cac_trend_pct, increase_word="increased"),
            self._trend_evidence("ROAS", snapshot.trend_pct, decrease_word="declined"),
        ]
        if (
            snapshot.cac_trend_pct is not None
            and snapshot.cac_trend_pct > 0
            and snapshot.trend_pct is not None
            and snapshot.trend_pct <= Decimal("-10")
            and snapshot.creatives
        ):
            return CampaignIssue(
                platform=platform,
                problem="CAC increased",
                reason="Creative fatigue",
                recommendation="Create a new campaign with refreshed creative",
                evidence=evidence,
            )
        if snapshot.cac_trend_pct is not None and snapshot.cac_trend_pct > 0:
            return CampaignIssue(
                platform=platform,
                problem="CAC increased",
                reason="Acquisition cost increased versus the prior period",
                recommendation="Tighten targeting and test a new campaign",
                evidence=evidence,
            )
        if snapshot.trend_pct is not None and snapshot.trend_pct <= Decimal("-10"):
            return CampaignIssue(
                platform=platform,
                problem="ROAS declined",
                reason="Campaign efficiency declined materially versus the prior period",
                recommendation="Launch a tightly scoped search campaign test",
                evidence=evidence,
            )
        if snapshot.roas is None or snapshot.roas < self._target_roas:
            return CampaignIssue(
                platform=platform,
                problem="ROAS is below target",
                reason=f"Observed ROAS does not meet the {self._target_roas} target",
                recommendation="Launch a tightly scoped search campaign test",
                evidence=evidence,
            )
        return CampaignIssue(
            platform=platform,
            problem="Scaling efficiency needs validation",
            reason="Current performance is healthy but has not been tested at incremental spend",
            recommendation="Run a controlled incremental-budget experiment",
            evidence=evidence,
        )

    @staticmethod
    def _trend_evidence(
        metric: str,
        trend: Decimal | None,
        *,
        increase_word: str = "increased",
        decrease_word: str = "decreased",
    ) -> str:
        if trend is None:
            return f"{metric} trend unavailable"
        if trend > 0:
            return f"{metric} {increase_word} {trend}% versus the prior period"
        if trend < 0:
            return f"{metric} {decrease_word} {abs(trend)}% versus the prior period"
        return f"{metric} was unchanged versus the prior period"
