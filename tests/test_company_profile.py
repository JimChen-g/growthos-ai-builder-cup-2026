from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.models.company import CompanyProfile
from integrations.models import DateRange


def test_company_profile_normalizes_currency() -> None:
    profile = CompanyProfile(
        company_name="Acme AI",
        industry="B2B SaaS",
        website="https://example.com",
        target_market="Seed-stage software teams",
        stage="seed",
        monthly_revenue="25000.00",
        currency="usd",
    )

    assert profile.monthly_revenue == Decimal("25000.00")
    assert profile.currency == "USD"


def test_company_profile_rejects_negative_revenue_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CompanyProfile(
            company_name="Acme AI",
            industry="B2B SaaS",
            website="https://example.com",
            target_market="Seed-stage software teams",
            stage="seed",
            monthly_revenue="-1",
            unexpected=True,
        )


def test_date_range_builds_equal_previous_period() -> None:
    period = DateRange(start="2026-01-01", end="2026-01-31")

    assert period.previous_period() == DateRange(start="2025-12-01", end="2025-12-31")


def test_settings_accept_blank_optional_cac_values() -> None:
    settings = Settings(
        _env_file=None,
        google_ads_target_cac="",
        meta_ads_target_cac="",
    )

    assert settings.google_ads_target_cac is None
    assert settings.meta_ads_target_cac is None
