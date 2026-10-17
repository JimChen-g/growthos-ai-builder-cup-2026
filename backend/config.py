"""Environment-backed Phase 1 configuration.

Secrets are optional at process start so the rules engine and tests can run without
external accounts. Individual integrations validate their required credentials when
their configuration object is constructed.
"""

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a local `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    # API protection. Without a token the API only answers loopback clients (local dev).
    api_access_token: SecretStr | None = None
    # Name recorded as the approver for token-authenticated decisions.
    api_principal_name: str = "founder"
    # Hosted judge/demo deployments: serve the Demo workspace only, refuse Live routes.
    public_demo_only: bool = False
    # Opt-in private, read-only workspaces for formally signed-in public visitors.
    # This does not enable the legacy shared workflows or advertising writes.
    live_self_service_enabled: bool = False
    live_storage_backend: Literal["local", "firestore"] = "local"
    live_data_retention_days: int = Field(default=7, ge=1, le=30)
    live_request_timeout_seconds: float = Field(default=45.0, gt=0, le=50)
    # Competition deployments use Firebase anonymous authentication. The backend derives the
    # workspace from the verified Firebase uid and never trusts a client-supplied tenant header.
    firebase_project_id: str | None = None
    firebase_auth_required: bool = False
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    data_source_mode: Literal["demo", "live"] = "demo"
    demo_fixture_path: Path = Path("data/demo_ai_customer_support.json")
    phase2_demo_fixture_path: Path = Path("data/demo_phase2_slowing_growth.json")
    phase5_demo_fixture_path: Path = Path("data/demo_phase5_polish.json")
    company_profile_path: Path = Path("data/company_profile.example.json")
    analysis_days: int = Field(default=30, ge=1, le=366)
    market_demand_score: Decimal = Field(default=Decimal("60"), ge=0, le=100)
    execution_capability_score: Decimal = Field(default=Decimal("62.5"), ge=0, le=100)

    stripe_secret_key: SecretStr | None = None
    stripe_currency: str = "usd"
    stripe_api_base_url: str = "https://api.stripe.com"

    google_ads_access_token: SecretStr | None = None
    google_ads_developer_token: SecretStr | None = None
    google_ads_customer_id: str | None = None
    google_ads_login_customer_id: str | None = None
    google_ads_client_id: str | None = None
    google_ads_client_secret: SecretStr | None = None
    google_ads_refresh_token: SecretStr | None = None
    google_ads_contains_eu_political_advertising: bool = False
    google_ads_api_version: str = "v25"
    google_ads_api_base_url: str = "https://googleads.googleapis.com"
    google_ads_currency: str = "USD"
    google_ads_target_roas: Decimal = Decimal("3.0")
    google_ads_target_cac: Decimal | None = None
    google_ads_target_conversion_rate_pct: Decimal = Decimal("3.0")

    meta_ads_access_token: SecretStr | None = None
    meta_ads_ad_account_id: str | None = None
    meta_ads_api_version: str | None = None
    meta_ads_api_base_url: str = "https://graph.facebook.com"
    meta_ads_currency: str = "USD"
    meta_ads_target_roas: Decimal = Decimal("3.0")
    meta_ads_target_cac: Decimal | None = None
    meta_ads_target_conversion_rate_pct: Decimal = Decimal("3.0")
    # Comma-separated Meta action types, first match wins (purchases, then leads/sign-ups).
    meta_ads_conversion_action_types: str = (
        "offsite_conversion.fb_pixel_purchase,omni_purchase,purchase,"
        "offsite_conversion.fb_pixel_lead,lead,onsite_conversion.lead_grouped,"
        "offsite_conversion.fb_pixel_complete_registration,complete_registration"
    )

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.6-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Opt-in Gemini narration for the briefing and agent replies (grounded, JSON-schema bound).
    gemini_narration_enabled: bool = False
    gemini_narration_model: str = "gemini-2.5-flash"

    vertex_ai_project_id: str | None = None
    vertex_ai_location: str = "global"
    vertex_ai_api_key: SecretStr | None = None
    # Gemini 2.5 Flash reached its published retirement date on 2026-10-16. The competition
    # candidate is a currently GA global model; deployment still requires a real project call.
    vertex_ai_model: str = "gemini-3.7-flash"
    vertex_ai_api_base_url: str = "https://aiplatform.googleapis.com"
    vertex_ai_request_timeout_seconds: float = Field(default=40.0, gt=0, le=55)

    competition_mode: bool = False
    firestore_database: str = "(default)"
    competition_session_daily_limit: int = Field(default=20, ge=1, le=1000)
    competition_global_daily_limit: int = Field(default=500, ge=1, le=100000)
    competition_max_evidence_items: int = Field(default=30, ge=2, le=100)
    competition_cache_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    competition_data_retention_days: int = Field(default=7, ge=1, le=30)

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_api_base_url: str = "https://api.openai.com/v1"

    perplexity_api_key: SecretStr | None = None
    perplexity_model: str = "sonar"
    perplexity_api_base_url: str = "https://api.perplexity.ai"

    x_bearer_token: SecretStr | None = None
    x_api_base_url: str = "https://api.x.com/2"
    social_request_timeout_seconds: float = Field(default=12.0, gt=0, le=60)

    geo_request_timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    geo_daily_provider_quota: int = Field(default=50, ge=1, le=10000)
    connection_vault_key: SecretStr | None = None
    connection_vault_path: Path = Path("data/private/connector_credentials.json.enc")
    demo_audit_anchor_receipt_path: Path = Path("data/demo_injective_anchor_receipt.json")
    live_audit_anchor_receipt_path: Path = Path("data/private/live_injective_anchor_receipt.json")
    demo_intelligence_history_path: Path = Path("data/demo_intelligence_history.json")
    live_intelligence_history_path: Path = Path("data/private/live_intelligence_history.json")

    @property
    def google_ads_has_credentials(self) -> bool:
        refresh = (
            self.google_ads_client_id,
            self.google_ads_client_secret,
            self.google_ads_refresh_token,
        )
        return bool(
            (self.google_ads_access_token or all(refresh))
            and self.google_ads_developer_token
            and self.google_ads_customer_id
        )

    @field_validator("google_ads_target_cac", "meta_ads_target_cac", mode="before")
    @classmethod
    def empty_optional_decimal_is_none(cls, value: object) -> object:
        """Allow optional numeric values to remain blank in `.env`."""

        return None if value == "" else value
