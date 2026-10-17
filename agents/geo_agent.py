"""Lightweight Generative Engine Optimization analysis for the Phase 2 demo."""

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field


class AIVisibilityObservation(BaseModel):
    """One externally collected AI answer reduced to auditable brand mentions."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=500)
    cited_brands: list[str] = Field(default_factory=list, max_length=20)


class CompetitorVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brand: str
    mention_count: int = Field(ge=1)
    share_of_prompts_pct: float = Field(ge=0, le=100)


class GEOAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    priority: int = Field(ge=1, le=3)
    action: str
    rationale: str


class GEOAnalysis(BaseModel):
    """AI visibility score, competitor comparison, and compact action plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    brand: str
    prompt_count: int = Field(ge=1)
    brand_mentions: int = Field(ge=0)
    visibility_score: float = Field(ge=0, le=100)
    brand_status: str
    competitors: list[CompetitorVisibility]
    reasons: list[str] = Field(min_length=1)
    action_plan: list[GEOAction] = Field(min_length=1, max_length=3)


class GEOAgent:
    """Compare brand mentions without attempting broad SEO or web crawling."""

    def analyze(self, brand: str, observations: list[AIVisibilityObservation]) -> GEOAnalysis:
        if not observations:
            raise ValueError("at least one AI visibility observation is required")

        normalized_brand = brand.casefold()
        mentions = sum(
            any(item.casefold() == normalized_brand for item in observation.cited_brands)
            for observation in observations
        )
        competitor_counts: Counter[str] = Counter()
        display_names: dict[str, str] = {}
        for observation in observations:
            for cited_brand in observation.cited_brands:
                key = cited_brand.casefold()
                if key == normalized_brand:
                    continue
                competitor_counts[key] += 1
                display_names.setdefault(key, cited_brand)

        total = len(observations)
        competitors = [
            CompetitorVisibility(
                brand=display_names[key],
                mention_count=count,
                share_of_prompts_pct=round(count / total * 100, 1),
            )
            for key, count in competitor_counts.most_common(5)
        ]
        visibility = round(mentions / total * 100, 1)
        if mentions == 0:
            status = "Missing"
            reasons = [
                "Brand was absent from every observed AI answer",
                "Competitors have stronger comparison-content or community signals",
            ]
        else:
            status = "Visible" if mentions == total else "Underrepresented"
            reasons = [
                f"Brand appeared in {mentions} of {total} tracked prompts",
                "Competitors still capture unowned answer positions",
            ]

        actions = [
            GEOAction(
                priority=1,
                action="Publish an evidence-backed competitor comparison page",
                rationale="Directly addresses high-intent comparison prompts",
            ),
            GEOAction(
                priority=2,
                action="Add quotable product facts and customer proof",
                rationale="Makes the brand easier for answer engines to cite",
            ),
            GEOAction(
                priority=3,
                action="Build credible community mentions around the tracked prompt set",
                rationale="Strengthens independent signals without expanding into complex SEO",
            ),
        ]
        return GEOAnalysis(
            brand=brand,
            prompt_count=total,
            brand_mentions=mentions,
            visibility_score=visibility,
            brand_status=status,
            competitors=competitors,
            reasons=reasons,
            action_plan=actions,
        )
