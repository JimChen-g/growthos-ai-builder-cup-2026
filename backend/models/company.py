"""Company context used by Phase 1 growth intelligence."""

from decimal import Decimal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class CompanyProfile(BaseModel):
    """Minimum company profile required by the Growth Score Engine."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    website: AnyHttpUrl
    target_market: str = Field(min_length=1, max_length=500)
    stage: str = Field(min_length=1, max_length=80)
    monthly_revenue: Decimal = Field(ge=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        """Store ISO-style currency codes consistently."""

        return value.upper()
