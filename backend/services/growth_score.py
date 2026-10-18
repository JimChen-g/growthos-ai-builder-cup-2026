"""Growth Score Engine V1: transparent, deterministic weighted rules."""

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class GrowthScoreStatus(StrEnum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"


class InsufficientGrowthDataError(ValueError):
    """A safe boundary: missing evidence cannot be used to plan or approve an action."""

    def __init__(self) -> None:
        super().__init__(
            "Growth data is insufficient for a decision. A comparable revenue baseline "
            "and observed advertising spend are required."
        )


class GrowthScoreInputs(BaseModel):
    """Known components are 0..100; None represents a missing observed input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revenue_momentum: Decimal | None = Field(ge=0, le=100)
    marketing_efficiency: Decimal | None = Field(ge=0, le=100)
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
        require_observed_data: bool = False,
    ) -> "GrowthScoreInputs":
        """Build inputs from normalized metrics plus two explicit assessed scores."""

        total_spend = sum((snapshot.spend for snapshot in ads), Decimal("0"))
        if require_observed_data and total_spend <= 0:
            # An absent denominator is not evidence of poor paid acquisition.
            marketing_efficiency = None
        elif not ads:
            marketing_efficiency = Decimal("50")
        else:
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
            revenue_momentum=(
                None
                if require_observed_data and revenue.growth_rate_pct is None
                else revenue.momentum_score
            ),
            marketing_efficiency=marketing_efficiency,
            market_demand=market_demand,
            execution_capability=execution_capability,
        )


class GrowthScoreResult(BaseModel):
    """Auditable contributions without inventing a total for missing observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: Decimal | None = Field(ge=0, le=100)
    band: GrowthScoreBand | None
    status: GrowthScoreStatus = GrowthScoreStatus.AVAILABLE
    missing_components: list[str] = Field(default_factory=list)
    method: str = "rules_v1"
    components: dict[str, Decimal | None]
    weights: dict[str, Decimal]
    contributions: dict[str, Decimal | None]

    def require_available(self) -> Decimal:
        """Protect legacy numeric decision consumers without inventing a fallback score."""
        if (
            self.status is not GrowthScoreStatus.AVAILABLE
            or self.score is None
            or self.band is None
            or any(value is None for value in self.components.values())
        ):
            raise InsufficientGrowthDataError()
        return self.score

    @model_validator(mode="after")
    def validate_availability(self) -> "GrowthScoreResult":
        missing = [name for name, value in self.components.items() if value is None]
        if self.status is GrowthScoreStatus.INSUFFICIENT_DATA:
            if self.score is not None or self.band is not None or not missing:
                raise ValueError("insufficient data must not have a score or risk band")
        elif self.score is None or self.band is None or missing:
            raise ValueError("available scores require all components and a risk band")
        if self.missing_components != missing:
            raise ValueError("missing_components must identify unavailable inputs")
        if any(self.contributions.get(name) is not None for name in missing):
            raise ValueError("unavailable inputs must not contribute a numeric score")
        return self


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
            name: self._rounded(value * WEIGHTS[name]) if value is not None else None
            for name, value in components.items()
        }
        missing = [name for name, value in components.items() if value is None]
        if missing:
            return GrowthScoreResult(
                score=None,
                band=None,
                status=GrowthScoreStatus.INSUFFICIENT_DATA,
                missing_components=missing,
                components=components,
                weights=WEIGHTS,
                contributions=contributions,
            )
        total = self._rounded(sum(
            (value for value in contributions.values() if value is not None), Decimal("0")
        ))
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
