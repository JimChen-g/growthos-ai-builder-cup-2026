"""Phase 3.1 real-intelligence connector contracts."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from backend.currency import normalize_currency_code, normalize_stripe_currency


class DataMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class DataProvenance(BaseModel):
    """Portable source metadata shown beside every founder-facing result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DataMode
    label: str
    detail: str


class ConnectorProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"
    GEMINI_VERTEX = "gemini_vertex"
    PERPLEXITY = "perplexity"
    X = "x"
    REDDIT = "reddit"
    GOOGLE_ADS = "google_ads"
    META_ADS = "meta_ads"
    STRIPE = "stripe"


class ConnectionState(StrEnum):
    CONNECTED = "connected"
    NEEDS_CONFIGURATION = "needs_configuration"
    FALLBACK_READY = "fallback_ready"


class ConnectionSetupKind(StrEnum):
    API_KEY = "api_key"
    CLOUD_PROJECT = "cloud_project"
    AUTOMATIC = "automatic"
    OAUTH = "oauth"
    RESTRICTED_KEY = "restricted_key"


class ConnectionAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    label: str


class ConnectionStatus(BaseModel):
    """Safe connection metadata; credentials are never serialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ConnectorProvider
    display_name: str
    category: str
    state: ConnectionState
    configured: bool
    auth_method: str
    configuration_source: str | None = None
    project_id: str | None = None
    location: str | None = None
    api_version: str | None = None
    selected_account_id: str | None = None
    currency: str | None = None
    account_environment: Literal["test", "live", "unknown"] = "unknown"
    saved_at: datetime | None = None
    expires_at: datetime | None = None
    storage_status: Literal[
        "saved", "missing", "expired", "unavailable", "server_environment", "unknown"
    ] = "unknown"
    accounts: list[ConnectionAccount] = Field(default_factory=list)
    supports_live_test: bool = False
    demo_available: bool = True
    setup_kind: ConnectionSetupKind
    setup_label: str
    setup_steps: list[str] = Field(min_length=1, max_length=3)
    estimated_setup_minutes: int = Field(ge=0, le=30)
    note: str


class ConnectionCenter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    vault_encryption_enabled: bool
    connections: list[ConnectionStatus]


class ConnectorCredentialRequest(BaseModel):
    """One secret accepted by the encrypted server-side connector vault."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    secret: SecretStr
    currency: str | None = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return normalize_stripe_currency(value) if value is not None else None

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        length = len(value.get_secret_value())
        if not 8 <= length <= 5000:
            raise ValueError("credential must contain between 8 and 5000 characters")
        return value


class VertexAIConnectionRequest(BaseModel):
    """Tenant-scoped Vertex AI project and optional API-key authentication."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    project_id: str = Field(
        min_length=4,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9-]{3,62}$",
    )
    location: str = Field(
        default="global",
        min_length=2,
        max_length=32,
        pattern=r"^(global|us|eu|[a-z][a-z0-9-]{1,31})$",
    )
    api_key: SecretStr | None = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        length = len(value.get_secret_value())
        if not 8 <= length <= 5000:
            raise ValueError("API key must contain between 8 and 5000 characters")
        return value


class MetaAdsConnectionRequest(BaseModel):
    """Meta read-access settings stored together in the encrypted credential vault."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    access_token: SecretStr
    ad_account_id: str = Field(min_length=1, max_length=64, pattern=r"^(?:act_)?\d+$")
    api_version: str = Field(pattern=r"^v\d{1,3}\.\d{1,2}$")
    currency: str = "USD"

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return normalize_currency_code(value)

    @field_validator("access_token")
    @classmethod
    def validate_access_token(cls, value: SecretStr) -> SecretStr:
        if not 8 <= len(value.get_secret_value()) <= 5000:
            raise ValueError("access token must contain between 8 and 5000 characters")
        return value

    @field_validator("ad_account_id")
    @classmethod
    def normalize_ad_account_id(cls, value: str) -> str:
        return value.removeprefix("act_")


class GoogleAdsConnectionRequest(BaseModel):
    """Tenant-owned Google Ads reporting credentials; no campaign mutation is enabled."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    developer_token: SecretStr
    customer_id: str = Field(min_length=1, max_length=32, pattern=r"^[\d-]+$")
    login_customer_id: str | None = Field(default=None, pattern=r"^[\d-]+$")
    access_token: SecretStr | None = None
    client_id: str | None = Field(default=None, min_length=1, max_length=500)
    client_secret: SecretStr | None = None
    refresh_token: SecretStr | None = None
    currency: str = "USD"

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return normalize_currency_code(value)

    @field_validator("developer_token", "access_token", "client_secret", "refresh_token")
    @classmethod
    def validate_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not 8 <= len(value.get_secret_value().strip()) <= 5000:
            raise ValueError("credential must contain between 8 and 5000 characters")
        return value

    @field_validator("customer_id", "login_customer_id")
    @classmethod
    def normalize_account(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("-", "")
        if not normalized.isdigit():
            raise ValueError("account ID must contain digits")
        return normalized

    @model_validator(mode="after")
    def require_authentication(self) -> "GoogleAdsConnectionRequest":
        refresh = (self.client_id, self.client_secret, self.refresh_token)
        if any(refresh) and not all(refresh):
            raise ValueError("OAuth requires client ID, client secret and refresh token together")
        if not self.access_token and not all(refresh):
            raise ValueError("an access token or complete OAuth credentials are required")
        return self


class AccountSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    account_id: str = Field(min_length=1, max_length=200)


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ConnectorProvider
    account_environment: Literal["test", "live", "unknown"] = "unknown"
    configured: bool
    checked_live: bool
    success: bool
    message: str
    latency_ms: int | None = Field(default=None, ge=0)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GeoCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    url: HttpUrl


class GeoPromptResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ConnectorProvider
    prompt: str
    answer: str
    brand_mentioned: bool
    mention_rank: int | None = Field(default=None, ge=1)
    brand_cited: bool
    citations: list[GeoCitation]
    latency_ms: int = Field(ge=0)
    error: str | None = None


class ProviderVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ConnectorProvider
    attempted_prompts: int = Field(ge=0)
    successful_prompts: int = Field(ge=0)
    brand_mentions: int = Field(ge=0)
    branded_citations: int = Field(ge=0)
    visibility_score: Decimal = Field(ge=0, le=100)
    status: str


DEFAULT_GEO_PROMPTS = [
    "What are the best AI customer support platforms for a growing SaaS company?",
    "Which customer service AI tools are strongest for automated support and agent productivity?",
    "Compare leading AI customer support software for a mid-market technology company.",
]


def default_geo_prompts(category: str) -> list[str]:
    """Neutral buyer prompts for the requested category (not always customer support)."""

    normalized = " ".join(category.split())
    if normalized.casefold() == "ai customer support":
        return list(DEFAULT_GEO_PROMPTS)
    return [
        f"What are the best {normalized} platforms for a growing company?",
        f"Which {normalized} tools are strongest right now, and why?",
        f"Compare leading {normalized} software for a mid-market company.",
    ]


class GeoScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def prompts_follow_category(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("prompts") and data.get("category"):
            return {**data, "prompts": default_geo_prompts(str(data["category"]))}
        return data

    brand: str = Field(min_length=2, max_length=120)
    website: HttpUrl
    category: str = Field(min_length=2, max_length=160)
    competitors: list[str] = Field(default_factory=list, max_length=10)
    prompts: list[str] = Field(
        default_factory=lambda: list(DEFAULT_GEO_PROMPTS), min_length=1, max_length=10
    )
    providers: list[ConnectorProvider] = Field(
        default_factory=lambda: [
            ConnectorProvider.OPENAI,
            ConnectorProvider.GEMINI,
            ConnectorProvider.PERPLEXITY,
        ],
        min_length=1,
        max_length=3,
    )
    mode: DataMode = DataMode.DEMO

    @field_validator("providers")
    @classmethod
    def geo_providers_only(cls, value: list[ConnectorProvider]) -> list[ConnectorProvider]:
        allowed = {
            ConnectorProvider.OPENAI,
            ConnectorProvider.GEMINI,
            ConnectorProvider.GEMINI_VERTEX,
            ConnectorProvider.PERPLEXITY,
        }
        if any(item not in allowed for item in value):
            raise ValueError(
                "GEO scans support OpenAI, Gemini AI Studio, Gemini Vertex AI, and Perplexity only"
            )
        unique = list(dict.fromkeys(value))
        if ConnectorProvider.GEMINI in unique and ConnectorProvider.GEMINI_VERTEX in unique:
            raise ValueError("choose either Gemini AI Studio or Gemini Vertex AI, not both")
        return unique

    @field_validator("prompts")
    @classmethod
    def validate_prompts(cls, value: list[str]) -> list[str]:
        if any(not 8 <= len(prompt.strip()) <= 500 for prompt in value):
            raise ValueError("each prompt must contain between 8 and 500 characters")
        return value


class GeoScanReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    mode: DataMode
    brand: str
    website: HttpUrl
    overall_visibility_score: Decimal = Field(ge=0, le=100)
    providers: list[ProviderVisibility]
    results: list[GeoPromptResult]
    provenance: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackCollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brand: str = Field(min_length=2, max_length=120)
    mode: DataMode = DataMode.DEMO
    sources: list[ConnectorProvider] = Field(
        default_factory=lambda: [ConnectorProvider.REDDIT, ConnectorProvider.X],
        min_length=1,
        max_length=2,
    )

    @field_validator("sources")
    @classmethod
    def feedback_sources_only(cls, value: list[ConnectorProvider]) -> list[ConnectorProvider]:
        allowed = {ConnectorProvider.REDDIT, ConnectorProvider.X}
        if any(item not in allowed for item in value):
            raise ValueError("feedback collection supports Reddit and X only")
        return list(dict.fromkeys(value))


class FeedbackSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ConnectorProvider
    status: str
    signal_count: int = Field(ge=0)
    collection_method: str
    error: str | None = None


class FeedbackCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    mode: DataMode
    brand: str
    sources: list[FeedbackSourceResult]
    signals: list["FeedbackSignal"]
    analysis: "FeedbackAnalysis | None" = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


from backend.models.feedback import FeedbackAnalysis, FeedbackSignal  # noqa: E402

FeedbackCollectionResponse.model_rebuild()
