"""Growth Score Engine V1: transparent, deterministic weighted rules."""

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from integrations.models import AdsSnapshot, RevenueSnapshot

WEIGHTS = {
    "revenue_momentum": Decimal("0.30"),
    "marketing_efficiency": Decimal("0.30"),
    "market_demand": Decimal("0.20"),
    "execution_capability": Decimal("0.20"),
}
TENTH = Decimal("0.1")


class GrowthScoreBand(StrEnum):
    AT_RISK = "at_risk"
    WATCH = "watch"
    PROMISING = "promising"
    STRONG = "strong"


class GrowthScoreInputs(BaseModel):
    """Four explainable component scores, each constrained to 0..100."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revenue_momentum: Decimal = Field(ge=0, le=100)
    marketing_efficiency: Decimal = Field(ge=0, le=100)
    market_demand: Decimal = Field(ge=0, le=100)
    execution_capability: Decimal = Field(ge=0, le=100)

    @classmethod
    def from_snapshots(
        cls,
        *,
        revenue: RevenueSnapshot,
        ads: list[AdsSnapshot],
        market_demand: Decimal,
        execution_capability: Decimal,
    ) -> "GrowthScoreInputs":
        """Build inputs from normalized metrics plus two explicit assessed scores."""

        if not ads:
            marketing_efficiency = Decimal("50")
        else:
            total_spend = sum((snapshot.spend for snapshot in ads), Decimal("0"))
            if total_spend > 0:
                marketing_efficiency = (
                    sum(
                        (snapshot.efficiency_score * snapshot.spend for snapshot in ads),
                        Decimal("0"),
                    )
                    / total_spend
                )
            else:
                marketing_efficiency = sum(
                    (snapshot.efficiency_score for snapshot in ads), Decimal("0")
                ) / Decimal(len(ads))

        return cls(
            revenue_momentum=revenue.momentum_score,
            marketing_efficiency=marketing_efficiency,
            market_demand=market_demand,
            execution_capability=execution_capability,
        )


class GrowthScoreResult(BaseModel):
    """Auditable result including every weighted contribution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: Decimal = Field(ge=0, le=100)
    band: GrowthScoreBand
    method: str = "rules_v1"
    components: dict[str, Decimal]
    weights: dict[str, Decimal]
    contributions: dict[str, Decimal]


class GrowthScoreEngine:
    """Apply the frozen V1 30/30/20/20 formula."""

    def score(self, inputs: GrowthScoreInputs) -> GrowthScoreResult:
        components = {
            "revenue_momentum": inputs.revenue_momentum,
            "marketing_efficiency": inputs.marketing_efficiency,
            "market_demand": inputs.market_demand,
            "execution_capability": inputs.execution_capability,
        }
        contributions = {
            name: self._rounded(value * WEIGHTS[name]) for name, value in components.items()
        }
        total = self._rounded(sum(contributions.values(), Decimal("0")))
        return GrowthScoreResult(
            score=total,
            band=self._band(total),
            components=components,
            weights=WEIGHTS,
            contributions=contributions,
        )

    @staticmethod
    def _rounded(value: Decimal) -> Decimal:
        return value.quantize(TENTH, rounding=ROUND_HALF_UP)

    @staticmethod
    def _band(score: Decimal) -> GrowthScoreBand:
        if score >= 80:
            return GrowthScoreBand.STRONG
        if score >= 60:
            return GrowthScoreBand.PROMISING
        if score >= 40:
            return GrowthScoreBand.WATCH
        return GrowthScoreBand.AT_RISK
