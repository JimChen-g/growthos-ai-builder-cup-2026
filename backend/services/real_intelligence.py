"""Phase 3.1 connection center, GEO runner, and live customer signals."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from agents.feedback_agent import FeedbackAgent
from backend.config import Settings
from backend.currency import normalize_currency_code, normalize_stripe_currency
from backend.models.connectors import (
    ConnectionAccount,
    ConnectionCenter,
    ConnectionSetupKind,
    ConnectionState,
    ConnectionStatus,
    ConnectionTestResult,
    ConnectorProvider,
    DataMode,
    FeedbackCollectionRequest,
    FeedbackCollectionResponse,
    FeedbackSourceResult,
    GeoCitation,
    GeoPromptResult,
    GeoScanReport,
    GeoScanRequest,
    GoogleAdsConnectionRequest,
    MetaAdsConnectionRequest,
    ProviderVisibility,
    VertexAIConnectionRequest,
)
from backend.models.feedback import FeedbackSignal, FeedbackSource
from backend.services.connector_security import (
    CredentialVault,
    CredentialVaultError,
    EncryptedCredentialVault,
    FirestoreCredentialVault,
    FirestoreTenantQuotaLimiter,
    QuotaExceededError,
    TenantQuotaLimiter,
)
from integrations.base import IntegrationAPIError, IntegrationError
from integrations.currency import AccountCurrencyMismatchError
from integrations.geo_providers import (
    GeminiSearchConfig,
    GeminiSearchProvider,
    GeoProviderClient,
    OpenAIWebSearchConfig,
    OpenAIWebSearchProvider,
    PerplexitySonarConfig,
    PerplexitySonarProvider,
    VertexAISearchConfig,
    VertexAISearchProvider,
)
from integrations.google_ads import GoogleAdsIntegration, GoogleAdsIntegrationConfig
from integrations.meta_ads import MetaAdsIntegration, MetaAdsIntegrationConfig
from integrations.models import AccountTimezoneError
from integrations.social_sources import (
    RedditSearchFallback,
    RedditSearchFallbackConfig,
    XRecentSearch,
    XRecentSearchConfig,
)
from integrations.stripe import stripe_account_environment

_CREDENTIAL_PROVIDERS = {
    ConnectorProvider.OPENAI,
    ConnectorProvider.GEMINI,
    ConnectorProvider.PERPLEXITY,
    ConnectorProvider.X,
    ConnectorProvider.STRIPE,
}


class UnsupportedConnectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedVertexAIConnection:
    project_id: str
    location: str
    api_key: SecretStr | None
    source: str
    runtime_identity: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedMetaAdsConnection:
    access_token: SecretStr
    ad_account_id: str
    api_version: str
    currency: str
    source: str


@dataclass(frozen=True, slots=True)
class ResolvedStripeConnection:
    secret: SecretStr
    currency: str
    source: str


@dataclass(frozen=True, slots=True)
class ResolvedGoogleAdsConnection:
    credentials: GoogleAdsConnectionRequest
    source: str


class ConnectorCredentialResolver:
    """Resolve tenant vault secrets; environment secrets belong only to local-demo."""

    def __init__(self, settings: Settings, vault: CredentialVault) -> None:
        self.settings = settings
        self.vault = vault

    def allows_server_credentials(self, tenant_id: str) -> bool:
        return tenant_id == "local-demo" and not self.settings.firebase_auth_required

    async def secret(
        self, tenant_id: str, provider: ConnectorProvider
    ) -> tuple[SecretStr | None, str | None]:
        if provider is ConnectorProvider.STRIPE:
            connection = await self.stripe_connection(tenant_id)
            return (connection.secret, connection.source) if connection else (None, None)
        stored = await self.vault.get(tenant_id, provider)
        if stored:
            return stored, "encrypted_vault"
        if not self.allows_server_credentials(tenant_id):
            return None, None
        environment = {
            ConnectorProvider.OPENAI: self.settings.openai_api_key,
            ConnectorProvider.GEMINI: self.settings.gemini_api_key,
            ConnectorProvider.PERPLEXITY: self.settings.perplexity_api_key,
            ConnectorProvider.X: self.settings.x_bearer_token,
            ConnectorProvider.STRIPE: self.settings.stripe_secret_key,
        }.get(provider)
        return (environment, "server_environment") if environment else (None, None)

    async def stripe_connection(self, tenant_id: str) -> ResolvedStripeConnection | None:
        stored = await self.vault.get(tenant_id, ConnectorProvider.STRIPE)
        if stored:
            raw = stored.get_secret_value()
            # Legacy records contained just the key and used USD for hosted accounts.
            if raw.startswith("rk_"):
                currency = (
                    self.settings.stripe_currency
                    if self.allows_server_credentials(tenant_id)
                    else "USD"
                )
                return ResolvedStripeConnection(
                    stored, normalize_stripe_currency(currency), "encrypted_vault"
                )
            try:
                payload = json.loads(raw)
                secret = payload["secret"]
                currency = normalize_stripe_currency(payload["currency"])
                if not isinstance(secret, str) or not secret.startswith("rk_"):
                    raise ValueError("invalid key")
            except (KeyError, TypeError, ValueError) as error:
                raise CredentialVaultError("stored Stripe configuration is invalid") from error
            return ResolvedStripeConnection(SecretStr(secret), currency, "encrypted_vault")
        if not self.allows_server_credentials(tenant_id) or not self.settings.stripe_secret_key:
            return None
        return ResolvedStripeConnection(
            self.settings.stripe_secret_key,
            normalize_stripe_currency(self.settings.stripe_currency),
            "server_environment",
        )

    async def vertex_connection(self, tenant_id: str) -> ResolvedVertexAIConnection | None:
        stored = await self.vault.get(tenant_id, ConnectorProvider.GEMINI_VERTEX)
        if stored:
            try:
                payload = json.loads(stored.get_secret_value())
                if not isinstance(payload, dict):
                    raise ValueError("invalid configuration")
                project_id = str(payload["project_id"])
                location = str(payload.get("location") or "global")
                api_key_value = payload.get("api_key")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise CredentialVaultError(
                    "stored Vertex AI connection configuration is invalid"
                ) from error
            if "auth_mode" in payload:
                if not await self._allows_linked_vertex_runtime(tenant_id, payload):
                    return None
                return ResolvedVertexAIConnection(
                    project_id=project_id,
                    location=location,
                    api_key=None,
                    source="encrypted_vault",
                    runtime_identity=True,
                )
            if not api_key_value and not self.allows_server_credentials(tenant_id):
                return None
            return ResolvedVertexAIConnection(
                project_id=project_id,
                location=location,
                api_key=SecretStr(str(api_key_value)) if api_key_value else None,
                source="encrypted_vault",
            )
        if not self.allows_server_credentials(tenant_id) or not self.settings.vertex_ai_project_id:
            return None
        return ResolvedVertexAIConnection(
            project_id=self.settings.vertex_ai_project_id,
            location=self.settings.vertex_ai_location,
            api_key=self.settings.vertex_ai_api_key,
            source="server_environment",
        )

    async def _allows_linked_vertex_runtime(self, tenant_id: str, payload: dict) -> bool:
        """Authorize only an administrator-created, expiring link for one hosted owner.

        Neither an environment opt-in alone nor a public project-only connection can
        grant runtime credentials. The encrypted marker and its lifecycle must both exist.
        """
        settings = self.settings
        if not (
            settings.firebase_auth_required
            and settings.live_self_service_enabled
            and settings.live_storage_backend == "firestore"
            and self.vault.enabled
            and settings.live_vertex_runtime_workspace_id
            and tenant_id == settings.live_vertex_runtime_workspace_id
            and settings.firebase_project_id
            and settings.vertex_ai_project_id == settings.firebase_project_id
            and payload.get("auth_mode") == "runtime_identity"
            and payload.get("project_id") == settings.vertex_ai_project_id
            and payload.get("location") == settings.vertex_ai_location
            and payload.get("api_key") is None
            and set(payload) <= {"auth_mode", "project_id", "location", "api_key"}
        ):
            return False
        metadata = getattr(self.vault, "metadata", None)
        if metadata is None:
            return False
        lifecycle = await metadata(tenant_id, ConnectorProvider.GEMINI_VERTEX)
        saved_at, expires_at = lifecycle.saved_at, lifecycle.expires_at
        if not (
            lifecycle.storage_status == "saved"
            and isinstance(saved_at, datetime)
            and isinstance(expires_at, datetime)
            and saved_at.tzinfo is not None
            and expires_at.tzinfo is not None
        ):
            return False
        now = datetime.now(UTC)
        # Firestore writes calculate the two timestamps separately; allow subsecond
        # recording drift, but never authorize beyond seven days from the saved time.
        seven_day_limit = saved_at + timedelta(days=7)
        return (
            saved_at <= now < min(expires_at, seven_day_limit)
            and expires_at <= seven_day_limit + timedelta(seconds=1)
        )

    async def meta_connection(self, tenant_id: str) -> ResolvedMetaAdsConnection | None:
        stored = await self.vault.get(tenant_id, ConnectorProvider.META_ADS)
        if stored:
            try:
                payload = MetaAdsConnectionRequest.model_validate_json(stored.get_secret_value())
            except ValueError as error:
                raise CredentialVaultError("stored Meta Ads configuration is invalid") from error
            return ResolvedMetaAdsConnection(
                access_token=payload.access_token,
                ad_account_id=payload.ad_account_id,
                api_version=payload.api_version,
                currency=payload.currency,
                source="encrypted_vault",
            )
        if not self.allows_server_credentials(tenant_id) or not all(
            (
                self.settings.meta_ads_access_token,
                self.settings.meta_ads_ad_account_id,
                self.settings.meta_ads_api_version,
            )
        ):
            return None
        return ResolvedMetaAdsConnection(
            access_token=self.settings.meta_ads_access_token,
            ad_account_id=self.settings.meta_ads_ad_account_id.removeprefix("act_"),
            api_version=self.settings.meta_ads_api_version,
            currency=normalize_currency_code(self.settings.meta_ads_currency),
            source="server_environment",
        )

    async def google_ads_connection(self, tenant_id: str) -> ResolvedGoogleAdsConnection | None:
        stored = await self.vault.get(tenant_id, ConnectorProvider.GOOGLE_ADS)
        if stored:
            try:
                credentials = GoogleAdsConnectionRequest.model_validate_json(
                    stored.get_secret_value()
                )
            except ValueError as error:
                raise CredentialVaultError("stored Google Ads configuration is invalid") from error
            return ResolvedGoogleAdsConnection(credentials, "encrypted_vault")
        if (
            not self.allows_server_credentials(tenant_id)
            or not self.settings.google_ads_has_credentials
        ):
            return None
        credentials = GoogleAdsConnectionRequest(
            developer_token=self.settings.google_ads_developer_token,
            customer_id=self.settings.google_ads_customer_id,
            login_customer_id=self.settings.google_ads_login_customer_id,
            access_token=self.settings.google_ads_access_token,
            client_id=self.settings.google_ads_client_id,
            client_secret=self.settings.google_ads_client_secret,
            refresh_token=self.settings.google_ads_refresh_token,
            currency=self.settings.google_ads_currency,
        )
        return ResolvedGoogleAdsConnection(credentials, "server_environment")


class ConnectionCenterService:
    def __init__(
        self,
        settings: Settings,
        vault: CredentialVault,
        resolver: ConnectorCredentialResolver,
        quota: TenantQuotaLimiter | FirestoreTenantQuotaLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.resolver = resolver
        self.quota = quota or TenantQuotaLimiter(settings.geo_daily_provider_quota)

    async def center(self, tenant_id: str) -> ConnectionCenter:
        ai_statuses = [
            await self._secret_status(
                tenant_id,
                ConnectorProvider.OPENAI,
                "ChatGPT / OpenAI",
                "AI visibility",
                "Server-side API key",
                "Responses API with required web search and URL citations.",
            ),
            await self._secret_status(
                tenant_id,
                ConnectorProvider.GEMINI,
                "Gemini · AI Studio",
                "AI visibility",
                "Quick connection · API key",
                "Gemini Developer API with grounded Google Search.",
            ),
            await self._vertex_status(tenant_id),
            await self._secret_status(
                tenant_id,
                ConnectorProvider.PERPLEXITY,
                "Perplexity",
                "AI visibility",
                "Server-side API key",
                "Sonar response with citations and search results.",
            ),
            await self._secret_status(
                tenant_id,
                ConnectorProvider.X,
                "X",
                "Customer intelligence",
                "App bearer token",
                "Recent Search reads public posts from the last seven days.",
            ),
        ]
        perplexity_secret, perplexity_source = await self.resolver.secret(
            tenant_id, ConnectorProvider.PERPLEXITY
        )
        reddit = ConnectionStatus(
            provider=ConnectorProvider.REDDIT,
            display_name="Reddit",
            category="Customer intelligence",
            state=(
                ConnectionState.FALLBACK_READY
                if perplexity_secret
                else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=perplexity_secret is not None,
            auth_method="Compliant domain-filtered web search",
            configuration_source=perplexity_source,
            supports_live_test=True,
            setup_kind=ConnectionSetupKind.AUTOMATIC,
            setup_label="Automatic with Perplexity",
            setup_steps=[
                "Connect Perplexity once",
                "GrowthOS restricts search to reddit.com",
            ],
            estimated_setup_minutes=0,
            note=(
                "Uses Perplexity Search restricted to reddit.com; it does not scrape Reddit "
                "or claim broad Data API access."
            ),
        )
        connections = [
            *ai_statuses,
            reddit,
            await self._google_ads_status(tenant_id),
            await self._meta_ads_status(tenant_id),
            await self._stripe_status(tenant_id),
        ]
        # Credentials remain resolved exclusively in this server-derived workspace.
        # Expose lifecycle evidence separately from the ephemeral read-only test result.
        metadata = getattr(self.vault, "metadata", None)
        if metadata is not None:
            async def with_metadata(item: ConnectionStatus) -> ConnectionStatus:
                if item.configuration_source == "server_environment":
                    return item.model_copy(update={"storage_status": "server_environment"})
                provider = (
                    ConnectorProvider.PERPLEXITY
                    if item.provider is ConnectorProvider.REDDIT
                    else item.provider
                )
                lifecycle = await metadata(tenant_id, provider)
                updates = {
                    "storage_status": lifecycle.storage_status,
                    "saved_at": lifecycle.saved_at,
                    "expires_at": lifecycle.expires_at,
                }
                if lifecycle.storage_status in {"missing", "expired", "unavailable"}:
                    # Expiry or disconnect can occur between the credential and lifecycle
                    # reads. Never retain a ready state after observing invalid storage.
                    updates.update(
                        configured=False,
                        state=ConnectionState.NEEDS_CONFIGURATION,
                        configuration_source=None,
                        selected_account_id=None,
                        currency=None,
                        accounts=[],
                        account_environment="unknown",
                    )
                return item.model_copy(update=updates)

            connections = list(await asyncio.gather(*(with_metadata(item) for item in connections)))
        return ConnectionCenter(
            tenant_id=tenant_id,
            vault_encryption_enabled=self.vault.enabled,
            connections=connections,
        )

    async def save_secret(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        secret: SecretStr,
        *,
        currency: str | None = None,
    ) -> ConnectionStatus:
        if provider not in _CREDENTIAL_PROVIDERS:
            raise UnsupportedConnectionError(
                f"{provider.value} does not accept a direct credential in this center"
            )
        if provider is ConnectorProvider.STRIPE and not secret.get_secret_value().startswith("rk_"):
            raise UnsupportedConnectionError("Use a restricted Stripe key with read-only access.")
        if provider is ConnectorProvider.STRIPE:
            existing = await self.resolver.stripe_connection(tenant_id)
            selected_currency = normalize_stripe_currency(
                currency or (existing.currency if existing else "USD")
            )
            secret = SecretStr(
                json.dumps({"secret": secret.get_secret_value(), "currency": selected_currency})
            )
        elif currency is not None:
            raise UnsupportedConnectionError("Currency is only supported for business connections.")
        await self.vault.put(tenant_id, provider, secret)
        center = await self.center(tenant_id)
        return next(item for item in center.connections if item.provider is provider)

    async def save_vertex_connection(
        self,
        tenant_id: str,
        request: VertexAIConnectionRequest,
    ) -> ConnectionStatus:
        if not request.api_key and not self.resolver.allows_server_credentials(tenant_id):
            raise UnsupportedConnectionError(
                "Your own Vertex AI API key is required for Live access."
            )
        payload = {
            "project_id": request.project_id,
            "location": request.location,
            "api_key": request.api_key.get_secret_value() if request.api_key else None,
        }
        await self.vault.put(
            tenant_id,
            ConnectorProvider.GEMINI_VERTEX,
            SecretStr(json.dumps(payload, separators=(",", ":"), sort_keys=True)),
        )
        return await self._vertex_status(tenant_id)

    async def save_meta_connection(
        self, tenant_id: str, request: MetaAdsConnectionRequest
    ) -> ConnectionStatus:
        payload = {
            "access_token": request.access_token.get_secret_value(),
            "ad_account_id": request.ad_account_id,
            "api_version": request.api_version,
            "currency": request.currency,
        }
        await self.vault.put(
            tenant_id,
            ConnectorProvider.META_ADS,
            SecretStr(json.dumps(payload, separators=(",", ":"), sort_keys=True)),
        )
        return await self._meta_ads_status(tenant_id)

    async def save_google_ads_connection(
        self, tenant_id: str, request: GoogleAdsConnectionRequest
    ) -> ConnectionStatus:
        payload = {
            name: value.get_secret_value() if isinstance(value, SecretStr) else value
            for name, value in request.model_dump(mode="python").items()
        }
        await self.vault.put(
            tenant_id, ConnectorProvider.GOOGLE_ADS, SecretStr(json.dumps(payload))
        )
        return await self._google_ads_status(tenant_id)

    async def disconnect(self, tenant_id: str, provider: ConnectorProvider) -> ConnectionStatus:
        if provider is ConnectorProvider.REDDIT:
            raise UnsupportedConnectionError(
                "Reddit uses Perplexity; disconnect Perplexity instead."
            )
        await self.vault.delete(tenant_id, provider)
        return next(
            item for item in (await self.center(tenant_id)).connections if item.provider is provider
        )

    async def growth_settings(self, tenant_id: str) -> Settings:
        """Resolve all business credentials for this tenant; never inherit another account."""

        connection = await self.resolver.meta_connection(tenant_id)
        updates = {
            "data_source_mode": "live",
            "stripe_secret_key": None,
            "stripe_currency": "USD",
            "google_ads_access_token": None,
            "google_ads_developer_token": None,
            "google_ads_customer_id": None,
            "google_ads_login_customer_id": None,
            "google_ads_client_id": None,
            "google_ads_client_secret": None,
            "google_ads_refresh_token": None,
            "google_ads_currency": "USD",
            "meta_ads_access_token": None,
            "meta_ads_ad_account_id": None,
            "meta_ads_api_version": None,
            "meta_ads_currency": "USD",
        }
        if self.resolver.allows_server_credentials(tenant_id):
            # Preserve the local developer's partially completed configuration. Hosted
            # identities always begin with the empty credential set above.
            for name in updates:
                if name != "data_source_mode":
                    updates[name] = getattr(self.settings, name)
        stripe = await self.resolver.stripe_connection(tenant_id)
        if stripe:
            updates["stripe_secret_key"] = stripe.secret
            updates["stripe_currency"] = stripe.currency
        google = await self.resolver.google_ads_connection(tenant_id)
        if google:
            for field, value in google.credentials.model_dump(mode="python").items():
                updates[f"google_ads_{field}"] = value
        if connection:
            updates.update(
                meta_ads_access_token=connection.access_token,
                meta_ads_ad_account_id=connection.ad_account_id,
                meta_ads_api_version=connection.api_version,
                meta_ads_currency=connection.currency,
            )
        return self.settings.model_copy(update=updates)

    async def business_connection_test(
        self, tenant_id: str, provider: ConnectorProvider
    ) -> ConnectionTestResult:
        """Perform bounded read-only checks with only the requesting tenant's credentials."""
        if provider is ConnectorProvider.META_ADS:
            return await self.meta_connection_test(tenant_id)
        settings = await self.growth_settings(tenant_id)
        configured = (
            bool(settings.stripe_secret_key)
            if provider is ConnectorProvider.STRIPE
            else (
                settings.google_ads_has_credentials
                if provider is ConnectorProvider.GOOGLE_ADS
                else False
            )
        )
        if not configured:
            return ConnectionTestResult(
                provider=provider,
                configured=False,
                checked_live=False,
                success=False,
                message="Connect this account before testing.",
            )
        started = monotonic()
        account_environment = "unknown"
        try:
            await self.quota.consume(tenant_id, provider)
            async with httpx.AsyncClient(timeout=15.0) as client:
                if provider is ConnectorProvider.STRIPE:
                    account_environment = stripe_account_environment(settings.stripe_secret_key)
                    authorization = f"Bearer {settings.stripe_secret_key.get_secret_value()}"
                    for endpoint in ("charges", "subscriptions"):
                        response = await client.get(
                            f"{settings.stripe_api_base_url.rstrip('/')}/v1/{endpoint}",
                            params={"limit": 1},
                            headers={"Authorization": authorization},
                        )
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict) or not isinstance(
                            payload.get("data"), list
                        ):
                            raise ValueError("invalid provider response")
                else:
                    adapter = GoogleAdsIntegration(
                        GoogleAdsIntegrationConfig(
                            access_token=settings.google_ads_access_token,
                            developer_token=settings.google_ads_developer_token,
                            customer_id=settings.google_ads_customer_id,
                            login_customer_id=settings.google_ads_login_customer_id,
                            oauth_client_id=settings.google_ads_client_id,
                            oauth_client_secret=settings.google_ads_client_secret,
                            oauth_refresh_token=settings.google_ads_refresh_token,
                            api_version=settings.google_ads_api_version,
                            api_base_url=settings.google_ads_api_base_url,
                            currency=settings.google_ads_currency,
                        ),
                        client=client,
                    )
                    await adapter.verify_reporting_metadata()
                    account_environment = adapter.account_environment
        except (IntegrationError, httpx.HTTPError, ValueError, QuotaExceededError) as error:
            return ConnectionTestResult(
                provider=provider,
                configured=True,
                checked_live=True,
                success=False,
                message=_safe_provider_error(error),
                latency_ms=int((monotonic() - started) * 1000),
            )
        return ConnectionTestResult(
            provider=provider,
            configured=True,
            checked_live=True,
            success=True,
            account_environment=account_environment,
            message="Live read-only connection verified.",
            latency_ms=int((monotonic() - started) * 1000),
        )

    async def consume_growth_quota(self, tenant_id: str) -> None:
        for provider in (
            ConnectorProvider.STRIPE,
            ConnectorProvider.GOOGLE_ADS,
            ConnectorProvider.META_ADS,
        ):
            await self.quota.consume(tenant_id, provider)

    async def meta_connection_test(self, tenant_id: str) -> ConnectionTestResult:
        """Verify ads_read with one bounded read; never create or update an ad."""

        connection = await self.resolver.meta_connection(tenant_id)
        if connection is None:
            return ConnectionTestResult(
                provider=ConnectorProvider.META_ADS,
                configured=False,
                checked_live=False,
                success=False,
                message="Meta Ads needs an access token, ad account ID, and API version.",
            )
        started = monotonic()
        url = (
            f"{self.settings.meta_ads_api_base_url.rstrip('/')}/{connection.api_version}"
            f"/act_{connection.ad_account_id}/insights"
        )
        try:
            await self.quota.consume(tenant_id, ConnectorProvider.META_ADS)
            async with httpx.AsyncClient(
                timeout=self.settings.social_request_timeout_seconds
            ) as client:
                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {connection.access_token.get_secret_value()}"
                    },
                    params={"fields": "spend", "date_preset": "yesterday", "limit": 1},
                )
            try:
                payload = response.json()
            except ValueError:
                payload = None
            error = payload.get("error") if isinstance(payload, dict) else None
            if response.is_error or error:
                expired = isinstance(error, dict) and error.get("code") == 190
                message = (
                    "Meta access token has expired or is invalid. Replace it and test again."
                    if expired or response.status_code == 401
                    else (
                        "Meta rejected this connection. "
                        "Check the ad account ID and ads_read permission."
                    )
                )
                success = False
            elif not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                success = False
                message = "Meta returned an invalid Insights response."
            else:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    adapter = MetaAdsIntegration(
                        MetaAdsIntegrationConfig(
                            access_token=connection.access_token,
                            ad_account_id=connection.ad_account_id,
                            api_version=connection.api_version,
                            api_base_url=self.settings.meta_ads_api_base_url,
                            currency=connection.currency,
                        ),
                        client=client,
                    )
                    await adapter.verify_reporting_metadata()
                success = True
                message = "Live Meta Ads read-only Insights request completed successfully."
        except QuotaExceededError as error:
            success = False
            message = _safe_provider_error(error)
        except httpx.HTTPError:
            success = False
            message = "Meta could not be reached. Try again."
        except IntegrationError as error:
            success = False
            message = _safe_provider_error(error)
        return ConnectionTestResult(
            provider=ConnectorProvider.META_ADS,
            configured=True,
            checked_live=True,
            success=success,
            message=message,
            latency_ms=int((monotonic() - started) * 1000),
        )

    async def select_account(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        account_id: str,
    ) -> ConnectionStatus:
        if provider not in {ConnectorProvider.GOOGLE_ADS, ConnectorProvider.META_ADS}:
            raise UnsupportedConnectionError("account selection is supported for ad accounts only")
        status = (
            await self._google_ads_status(tenant_id)
            if provider is ConnectorProvider.GOOGLE_ADS
            else await self._meta_ads_status(tenant_id)
        )
        if account_id not in {item.account_id for item in status.accounts}:
            raise UnsupportedConnectionError("account is not available to this connection")
        # A connection stores its chosen account durably with its credentials. Only that
        # verified account is offered; selecting it again is an idempotent operation.
        return (
            await self._google_ads_status(tenant_id)
            if provider is ConnectorProvider.GOOGLE_ADS
            else await self._meta_ads_status(tenant_id)
        )

    async def _secret_status(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        display_name: str,
        category: str,
        auth_method: str,
        note: str,
    ) -> ConnectionStatus:
        secret, source = await self.resolver.secret(tenant_id, provider)
        configured = secret is not None
        setup = {
            ConnectorProvider.OPENAI: (
                "Paste one OpenAI project key",
                ["Create a project API key", "Paste it once", "Test the live connection"],
            ),
            ConnectorProvider.GEMINI: (
                "Paste one Gemini API key",
                ["Create an AI Studio key", "Paste it once", "Test grounded search"],
            ),
            ConnectorProvider.PERPLEXITY: (
                "Paste one Perplexity API key",
                ["Create a Perplexity API key", "Paste it once", "Test citations"],
            ),
            ConnectorProvider.X: (
                "Paste one X bearer token",
                ["Create an X app", "Paste its bearer token", "Test Recent Search"],
            ),
        }[provider]
        return ConnectionStatus(
            provider=provider,
            display_name=display_name,
            category=category,
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method=auth_method,
            configuration_source=source,
            supports_live_test=True,
            setup_kind=ConnectionSetupKind.API_KEY,
            setup_label=setup[0],
            setup_steps=setup[1],
            estimated_setup_minutes=2,
            note=note,
        )

    async def _vertex_status(self, tenant_id: str) -> ConnectionStatus:
        connection = await self.resolver.vertex_connection(tenant_id)
        configured = connection is not None
        runtime_identity = bool(connection and connection.runtime_identity)
        allows_adc = self.resolver.allows_server_credentials(tenant_id)
        uses_api_key = bool(connection and connection.api_key) or not allows_adc
        return ConnectionStatus(
            provider=ConnectorProvider.GEMINI_VERTEX,
            display_name="Gemini · Vertex AI",
            category="AI visibility",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method=(
                "Enterprise connection · Linked project identity"
                if runtime_identity
                else
                "Enterprise connection · Project + API key"
                if uses_api_key
                else "Enterprise connection · Project + ADC"
            ),
            configuration_source=connection.source if connection else None,
            project_id=connection.project_id if connection else None,
            location=connection.location if connection else None,
            supports_live_test=True,
            setup_kind=ConnectionSetupKind.CLOUD_PROJECT,
            setup_label="Connect one Google Cloud project",
            setup_steps=[
                "Runtime identity linked to this workspace",
                "Test Google Search grounding",
                "Disconnect to revoke workspace access",
            ] if runtime_identity else [
                "Enter the project ID",
                "Use server ADC or paste one API key"
                if allows_adc
                else "Paste your own Vertex AI API key",
                "Test Google Search grounding",
            ],
            estimated_setup_minutes=5,
            note=(
                "Gemini on Vertex AI with Google Search grounding; global is the simplest "
                "default, while regional endpoints support data-location controls."
            ),
        )

    async def _google_ads_status(self, tenant_id: str) -> ConnectionStatus:
        connection = await self.resolver.google_ads_connection(tenant_id)
        configured = connection is not None
        account_id = connection.credentials.customer_id if connection else None
        accounts = (
            [
                ConnectionAccount(
                    account_id=account_id,
                    label=_account_label("Google Ads", account_id),
                )
            ]
            if account_id
            else []
        )
        selected = account_id
        return ConnectionStatus(
            provider=ConnectorProvider.GOOGLE_ADS,
            display_name="Google Ads",
            category="Business data",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method="Workspace-owned OAuth or access token (Cloud project API access)",
            configuration_source=connection.source if connection else None,
            selected_account_id=selected,
            currency=connection.credentials.currency if connection else None,
            accounts=accounts,
            supports_live_test=True,
            setup_kind=ConnectionSetupKind.OAUTH,
            setup_label="Connect your Google Ads account",
            setup_steps=[
                "Create OAuth credentials in a Cloud project with Google Ads API access",
                "Save your credentials and customer ID in this workspace",
                "Test the read-only connection",
            ],
            estimated_setup_minutes=3,
            note="This connection reads reporting data only; no campaigns or budgets are changed.",
        )

    async def _meta_ads_status(self, tenant_id: str) -> ConnectionStatus:
        connection = await self.resolver.meta_connection(tenant_id)
        configured = connection is not None
        account_id = connection.ad_account_id if connection else None
        accounts = (
            [ConnectionAccount(account_id=account_id, label=_account_label("Meta Ads", account_id))]
            if account_id
            else []
        )
        selected = account_id
        return ConnectionStatus(
            provider=ConnectorProvider.META_ADS,
            display_name="Meta Ads",
            category="Business data",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method="Read-only Meta system-user access token",
            configuration_source=connection.source if connection else None,
            api_version=connection.api_version if connection else None,
            currency=connection.currency if connection else None,
            selected_account_id=selected,
            accounts=accounts,
            supports_live_test=True,
            setup_kind=ConnectionSetupKind.API_KEY,
            setup_label="Connect a Meta Ads account",
            setup_steps=[
                "Create a Meta system-user access token with ads_read",
                "Save the token, ad account ID, and API version",
                "Test the read-only connection",
            ],
            estimated_setup_minutes=3,
            note=(
                "Credentials stay encrypted; the live test reads Insights "
                "without changing ads or budgets."
            ),
        )

    async def _stripe_status(self, tenant_id: str) -> ConnectionStatus:
        connection = await self.resolver.stripe_connection(tenant_id)
        configured = connection is not None
        return ConnectionStatus(
            provider=ConnectorProvider.STRIPE,
            display_name="Stripe",
            category="Business data",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method="Restricted secret key",
            configuration_source=connection.source if connection else None,
            currency=connection.currency if connection else None,
            account_environment=stripe_account_environment(
                connection.secret if connection else None
            ),
            supports_live_test=True,
            setup_kind=ConnectionSetupKind.RESTRICTED_KEY,
            setup_label="Connect a restricted Stripe key",
            setup_steps=[
                "Create a restricted Stripe key",
                "Allow read-only charges and subscriptions",
                "Save the key in this workspace and test the connection",
            ],
            estimated_setup_minutes=2,
            note="Read-only revenue intelligence connection.",
        )


class GeoProviderFactory:
    def __init__(self, settings: Settings, resolver: ConnectorCredentialResolver) -> None:
        self.settings = settings
        self.resolver = resolver

    async def build(self, tenant_id: str, provider: ConnectorProvider) -> GeoProviderClient | None:
        timeout = self.settings.geo_request_timeout_seconds
        if self.settings.firebase_auth_required:
            timeout = min(timeout, 30.0)
        if provider is ConnectorProvider.GEMINI_VERTEX:
            connection = await self.resolver.vertex_connection(tenant_id)
            if not connection:
                return None
            return VertexAISearchProvider(
                VertexAISearchConfig(
                    project_id=connection.project_id,
                    location=connection.location,
                    api_key=connection.api_key,
                    model=self.settings.vertex_ai_model,
                    api_base_url=self.settings.vertex_ai_api_base_url,
                    request_timeout_seconds=timeout,
                )
            )
        secret, _ = await self.resolver.secret(tenant_id, provider)
        if not secret:
            return None
        if provider is ConnectorProvider.OPENAI:
            return OpenAIWebSearchProvider(
                OpenAIWebSearchConfig(
                    api_key=secret,
                    model=self.settings.openai_model,
                    api_base_url=self.settings.openai_api_base_url,
                    request_timeout_seconds=timeout,
                )
            )
        if provider is ConnectorProvider.GEMINI:
            return GeminiSearchProvider(
                GeminiSearchConfig(
                    api_key=secret,
                    model=self.settings.gemini_model,
                    api_base_url=self.settings.gemini_api_base_url,
                    request_timeout_seconds=timeout,
                )
            )
        if provider is ConnectorProvider.PERPLEXITY:
            return PerplexitySonarProvider(
                PerplexitySonarConfig(
                    api_key=secret,
                    model=self.settings.perplexity_model,
                    api_base_url=self.settings.perplexity_api_base_url,
                    request_timeout_seconds=timeout,
                )
            )
        raise UnsupportedConnectionError(f"{provider.value} is not a GEO provider")


class GeoPromptRunner:
    """Run identical neutral prompts across providers, then score observable mentions."""

    def __init__(
        self,
        factory: GeoProviderFactory,
        quota: TenantQuotaLimiter | FirestoreTenantQuotaLimiter,
        *,
        request_timeout_seconds: float = 30.0,
        total_timeout_seconds: float = 40.0,
    ) -> None:
        self.factory = factory
        self.quota = quota
        self.request_timeout_seconds = request_timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self._call_slots = asyncio.Semaphore(6)
        self._tenant_call_slots: dict[str, tuple[asyncio.Semaphore, int]] = {}

    @asynccontextmanager
    async def _bounded_call(self, tenant_id: str) -> AsyncIterator[None]:
        # The runner is shared by the app, not created for each request. Keep the
        # process-wide cap, but leave capacity for another workspace even when
        # one workspace submits several scans/checks. Count waiting users too;
        # otherwise releasing an active call could create a second semaphore.
        slots, users = self._tenant_call_slots.get(tenant_id, (asyncio.Semaphore(3), 0))
        self._tenant_call_slots[tenant_id] = (slots, users + 1)
        try:
            async with slots, self._call_slots:
                yield
        finally:
            _, users = self._tenant_call_slots[tenant_id]
            if users == 1:
                del self._tenant_call_slots[tenant_id]
            else:
                self._tenant_call_slots[tenant_id] = (slots, users - 1)

    async def scan(self, tenant_id: str, request: GeoScanRequest) -> GeoScanReport:
        deadline = monotonic() + self.total_timeout_seconds

        async def provider_results(provider: ConnectorProvider) -> list[GeoPromptResult]:
            if request.mode is DataMode.DEMO:
                return self._demo_results(provider, request)
            return await self._live_results(tenant_id, provider, request, deadline=deadline)

        # Bounded prompt concurrency lets the default three prompts fit the HTTP deadline.
        per_provider = await asyncio.gather(
            *(provider_results(provider) for provider in request.providers)
        )
        all_results: list[GeoPromptResult] = []
        summaries: list[ProviderVisibility] = []
        for provider, results in zip(request.providers, per_provider, strict=True):
            all_results.extend(results)
            summaries.append(_provider_summary(provider, results))
        completed_scores = [
            item.visibility_score for item in summaries if item.successful_prompts > 0
        ]
        overall = (
            sum(completed_scores, Decimal("0")) / len(completed_scores)
            if completed_scores
            else Decimal("0")
        )
        return GeoScanReport(
            tenant_id=tenant_id,
            mode=request.mode,
            brand=request.brand,
            website=request.website,
            overall_visibility_score=overall.quantize(Decimal("0.1")),
            providers=summaries,
            results=all_results,
            provenance=(
                "Synthetic deterministic preview; no provider API was called."
                if request.mode is DataMode.DEMO
                else (
                    "Live mode: only successful provider responses are measured; synthetic "
                    "answers are never substituted."
                )
            ),
        )

    async def connection_test(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
    ) -> ConnectionTestResult:
        started = monotonic()
        try:
            client = await self.factory.build(tenant_id, provider)
            if not client:
                return ConnectionTestResult(
                    provider=provider,
                    configured=False,
                    checked_live=False,
                    success=False,
                    message="No server-side credential is configured.",
                )
            try:
                async with asyncio.timeout(self.request_timeout_seconds):
                    async with self._bounded_call(tenant_id):
                        await self.quota.consume(tenant_id, provider)
                        answer = await client.ask(
                            "Name one widely used customer support software product."
                        )
            finally:
                await client.aclose()
        except (
            IntegrationError,
            QuotaExceededError,
            UnsupportedConnectionError,
            TimeoutError,
        ) as error:
            return ConnectionTestResult(
                provider=provider,
                configured=True,
                checked_live=True,
                success=False,
                message=_safe_provider_error(error),
                latency_ms=int((monotonic() - started) * 1000),
            )
        return ConnectionTestResult(
            provider=provider,
            configured=True,
            checked_live=True,
            success=True,
            message=(
                "Live grounded response completed successfully."
                if answer.citations
                else "Live response completed, but no source citations were returned."
            ),
            latency_ms=int((monotonic() - started) * 1000),
        )

    async def _live_results(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        request: GeoScanRequest,
        *,
        deadline: float | None = None,
    ) -> list[GeoPromptResult]:
        deadline = deadline or (monotonic() + self.total_timeout_seconds)
        timeout_message = "The provider request timed out. Retry the unavailable prompts."
        results = [_error_result(provider, prompt, timeout_message) for prompt in request.prompts]
        client = None
        # Perplexity can reject a burst on a single workspace key even when a
        # one-call connection check succeeds. Serialize its prompts without
        # retries or extra quota; other providers retain bounded parallel reads.
        provider_slots = asyncio.Semaphore(
            1 if provider is ConnectorProvider.PERPLEXITY else 3
        )
        remaining_prompts = len(request.prompts)

        async def run_prompt(index: int, prompt: str) -> None:
            nonlocal remaining_prompts
            started = monotonic()
            try:
                async with provider_slots, self._bounded_call(tenant_id):
                    remaining_seconds = deadline - monotonic()
                    if remaining_seconds <= 0:
                        raise TimeoutError
                    # Serialized prompts share the remaining wall-clock budget,
                    # so a slow first answer cannot consume all later attempts.
                    # Queue time counts toward the scan deadline; completed fast
                    # calls leave their unused time for the remaining prompts.
                    prompt_budget = (
                        remaining_seconds / remaining_prompts
                        if provider is ConnectorProvider.PERPLEXITY
                        else remaining_seconds
                    )
                    try:
                        async with asyncio.timeout(
                            min(self.request_timeout_seconds, prompt_budget)
                        ):
                            await self.quota.consume(tenant_id, provider)
                            raw = await client.ask(prompt)
                    finally:
                        remaining_prompts -= 1
                results[index] = _measured_result(
                    provider,
                    prompt,
                    raw.answer,
                    raw.citations,
                    request.brand,
                    request.competitors,
                    str(request.website),
                    int((monotonic() - started) * 1000),
                )
            except (IntegrationError, QuotaExceededError, TimeoutError) as error:
                results[index] = _error_result(
                    provider,
                    prompt,
                    _safe_provider_error(error),
                    int((monotonic() - started) * 1000),
                )

        try:
            async with asyncio.timeout(max(0.001, deadline - monotonic())):
                client = await self.factory.build(tenant_id, provider)
                if not client:
                    return [
                        _error_result(
                            provider, prompt, "No credential is configured for live mode."
                        )
                        for prompt in request.prompts
                    ]
                await asyncio.gather(
                    *(run_prompt(i, prompt) for i, prompt in enumerate(request.prompts))
                )
        except TimeoutError:
            # Preserve completed, measured answers; uncompleted slots remain explicit errors.
            pass
        except (IntegrationError, UnsupportedConnectionError, CredentialVaultError) as error:
            return [
                _error_result(provider, prompt, _safe_provider_error(error))
                for prompt in request.prompts
            ]
        finally:
            if client:
                await client.aclose()
        return results

    def _demo_results(
        self, provider: ConnectorProvider, request: GeoScanRequest
    ) -> list[GeoPromptResult]:
        competitor = request.competitors[0] if request.competitors else "Example Competitor"
        answers = [
            f"For this synthetic preview, {request.brand} and {competitor} are included.",
            f"This synthetic answer mentions {competitor} but omits the measured brand.",
            f"A synthetic shortlist places {competitor} first and {request.brand} second.",
        ]
        citations = [GeoCitation(title=f"{request.brand} website", url=request.website)]
        return [
            _measured_result(
                provider,
                prompt,
                answers[index % len(answers)],
                citations if index == 0 else [],
                request.brand,
                request.competitors,
                str(request.website),
                0,
            )
            for index, prompt in enumerate(request.prompts)
        ]


class FeedbackCollector:
    def __init__(
        self,
        settings: Settings,
        resolver: ConnectorCredentialResolver,
        quota: TenantQuotaLimiter,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.quota = quota
        self.agent = FeedbackAgent()

    async def collect(
        self, tenant_id: str, request: FeedbackCollectionRequest
    ) -> FeedbackCollectionResponse:
        signals: list[FeedbackSignal] = []
        source_results: list[FeedbackSourceResult] = []
        for provider in request.sources:
            if request.mode is DataMode.DEMO:
                collected = _demo_feedback(provider, request.brand)
                method = "synthetic fixture"
                error = None
            else:
                collected, method, error = await self._live_source(
                    tenant_id, provider, request.brand
                )
            signals.extend(collected)
            source_results.append(
                FeedbackSourceResult(
                    provider=provider,
                    status="completed" if not error else "unavailable",
                    signal_count=len(collected),
                    collection_method=method,
                    error=error,
                )
            )
        return FeedbackCollectionResponse(
            tenant_id=tenant_id,
            mode=request.mode,
            brand=request.brand,
            sources=source_results,
            signals=signals,
            analysis=self.agent.analyze(signals) if signals else None,
        )

    async def connection_test(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
    ) -> ConnectionTestResult:
        started = monotonic()
        _, _, error = await self._live_source(tenant_id, provider, "Intercom")
        credential_provider = (
            ConnectorProvider.PERPLEXITY
            if provider is ConnectorProvider.REDDIT
            else ConnectorProvider.X
        )
        secret, _ = await self.resolver.secret(tenant_id, credential_provider)
        return ConnectionTestResult(
            provider=provider,
            configured=secret is not None,
            checked_live=secret is not None,
            success=error is None,
            message=error or "Live public-signal request completed successfully.",
            latency_ms=int((monotonic() - started) * 1000) if secret else None,
        )

    async def _live_source(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        brand: str,
    ) -> tuple[list[FeedbackSignal], str, str | None]:
        credential_provider = (
            ConnectorProvider.PERPLEXITY
            if provider is ConnectorProvider.REDDIT
            else ConnectorProvider.X
        )
        secret, _ = await self.resolver.secret(tenant_id, credential_provider)
        method = (
            "Perplexity Search restricted to reddit.com"
            if provider is ConnectorProvider.REDDIT
            else "X Recent Search API"
        )
        if not secret:
            return [], method, "No credential is configured for live mode."
        client = None
        try:
            await self.quota.consume(tenant_id, credential_provider)
            if provider is ConnectorProvider.REDDIT:
                client = RedditSearchFallback(
                    RedditSearchFallbackConfig(
                        perplexity_api_key=secret,
                        api_base_url=self.settings.perplexity_api_base_url,
                        request_timeout_seconds=(self.settings.social_request_timeout_seconds),
                    )
                )
            else:
                client = XRecentSearch(
                    XRecentSearchConfig(
                        bearer_token=secret,
                        api_base_url=self.settings.x_api_base_url,
                        request_timeout_seconds=(self.settings.social_request_timeout_seconds),
                    )
                )
            return await client.collect(brand), method, None
        except (IntegrationError, QuotaExceededError) as error:
            if (
                provider is ConnectorProvider.X
                and isinstance(error, IntegrationAPIError)
                and error.status_code in {401, 403}
            ):
                return (
                    [],
                    method,
                    "X rejected the bearer token. Replace it with a valid token and test again.",
                )
            return [], method, _safe_provider_error(error)
        finally:
            if client:
                await client.aclose()


@dataclass(slots=True)
class RealIntelligenceRuntime:
    connections: ConnectionCenterService
    geo: GeoPromptRunner
    feedback: FeedbackCollector


def build_real_intelligence_runtime(settings: Settings | None = None) -> RealIntelligenceRuntime:
    selected = settings or Settings()
    if selected.live_storage_backend == "firestore":
        if not selected.firebase_project_id or not selected.connection_vault_key:
            raise RuntimeError("Hosted Live requires a Firebase project and credential vault key")
        vault: CredentialVault = FirestoreCredentialVault(
            selected.firebase_project_id,
            selected.firestore_database,
            selected.connection_vault_key,
            selected.live_data_retention_days,
        )
        quota = FirestoreTenantQuotaLimiter(
            selected.firebase_project_id,
            selected.firestore_database,
            selected.geo_daily_provider_quota,
        )
    else:
        if selected.firebase_auth_required and selected.live_self_service_enabled:
            raise RuntimeError("Hosted Live requires persistent Firestore connector storage")
        vault = EncryptedCredentialVault(
            selected.connection_vault_path, selected.connection_vault_key
        )
        quota = TenantQuotaLimiter(selected.geo_daily_provider_quota)
    resolver = ConnectorCredentialResolver(selected, vault)
    return RealIntelligenceRuntime(
        connections=ConnectionCenterService(selected, vault, resolver, quota),
        geo=GeoPromptRunner(
            GeoProviderFactory(selected, resolver),
            quota,
            request_timeout_seconds=min(30.0, selected.geo_request_timeout_seconds),
            total_timeout_seconds=max(0.1, min(40.0, selected.live_request_timeout_seconds - 5.0)),
        ),
        feedback=FeedbackCollector(selected, resolver, quota),
    )


def _safe_provider_error(error: Exception) -> str:
    """Never return raw provider exceptions, URLs, request payloads or secrets to browsers."""
    if isinstance(error, QuotaExceededError):
        return "Daily provider usage limit reached. Try again tomorrow."
    if isinstance(error, AccountCurrencyMismatchError):
        return str(error)
    if isinstance(error, AccountTimezoneError):
        return str(error)
    if isinstance(error, TimeoutError):
        return "The provider request timed out. Retry the unavailable prompts."
    status = (
        error.status_code
        if isinstance(error, IntegrationAPIError)
        else (error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None)
    )
    if isinstance(error, IntegrationAPIError) and error.provider == "google_ads":
        if error.operation == "oauth_refresh":
            return (
                "Google OAuth could not refresh this connection. Reauthorize Google Ads "
                "and save a matching client ID, client secret and refresh token."
            )
        if error.error_code in {"USER_PERMISSION_DENIED", "CUSTOMER_NOT_FOUND"}:
            return (
                "Google OAuth succeeded, but the authorized user cannot read this customer ID. "
                "Confirm the target account; if it is reached through a manager account, enter "
                "that manager customer ID."
            )
        if error.error_code == "NOT_ADS_USER":
            return (
                "Google OAuth succeeded, but the authorizing Google user has no Google Ads "
                "account access. Authorize with a Google Ads user."
            )
        if error.error_code == "ACCESS_TOKEN_SCOPE_INSUFFICIENT":
            return (
                "Google OAuth succeeded without the Google Ads scope. Reauthorize with the "
                "adwords scope and save the new refresh token."
            )
        if error.error_code in {
            "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION",
            "DEVELOPER_TOKEN_NOT_APPROVED",
        }:
            return (
                "Google OAuth succeeded, but the OAuth client project is not approved for this "
                "Ads account. Confirm Explorer or higher API access and retry after it propagates."
            )
        if error.operation == "account_metadata" and status == 403:
            return (
                "Google OAuth succeeded, but Google Ads denied access to this customer. Confirm "
                "the customer ID, the authorizing user's Ads access and any manager customer ID; "
                "new Explorer access may also need time to propagate."
            )
    if status in {401, 403}:
        return "The provider rejected this connection. Check your credentials and account access."
    if status == 429:
        return "The provider's usage limit was reached. Try again later."
    return "The provider request could not be completed. Check the connection and try again."


def _measured_result(
    provider: ConnectorProvider,
    prompt: str,
    answer: str,
    citations: list[GeoCitation],
    brand: str,
    competitors: list[str],
    website: str,
    latency_ms: int,
) -> GeoPromptResult:
    mentioned = _mentions(answer, brand)
    rank = _mention_rank(answer, brand, competitors) if mentioned else None
    owned_domain = _normalized_host(urlparse(website).hostname or "")
    brand_cited = bool(owned_domain) and any(
        _same_site(_citation_domain(citation), owned_domain) for citation in citations
    )
    return GeoPromptResult(
        provider=provider,
        prompt=prompt,
        answer=answer,
        brand_mentioned=mentioned,
        mention_rank=rank,
        brand_cited=brand_cited,
        citations=citations,
        latency_ms=latency_ms,
    )


def _error_result(
    provider: ConnectorProvider,
    prompt: str,
    error: str,
    latency_ms: int = 0,
) -> GeoPromptResult:
    return GeoPromptResult(
        provider=provider,
        prompt=prompt,
        answer="",
        brand_mentioned=False,
        brand_cited=False,
        citations=[],
        latency_ms=latency_ms,
        error=error[:300],
    )


def _provider_summary(
    provider: ConnectorProvider, results: list[GeoPromptResult]
) -> ProviderVisibility:
    successful = [item for item in results if not item.error]
    mentions = [item for item in successful if item.brand_mentioned]
    branded_citations = sum(item.brand_cited for item in successful)
    if not successful:
        score = Decimal("0")
        status = "unavailable"
    else:
        coverage = Decimal(len(mentions)) / Decimal(len(successful)) * Decimal("60")
        rank = sum(
            (Decimal(max(0, 6 - (item.mention_rank or 6))) / Decimal("5") * Decimal("25"))
            for item in successful
        ) / Decimal(len(successful))
        citation = Decimal(branded_citations) / Decimal(len(successful)) * Decimal("15")
        score = (coverage + rank + citation).quantize(Decimal("0.1"))
        status = "completed" if len(successful) == len(results) else "partial"
    return ProviderVisibility(
        provider=provider,
        attempted_prompts=len(results),
        successful_prompts=len(successful),
        brand_mentions=len(mentions),
        branded_citations=branded_citations,
        visibility_score=score,
        status=status,
    )


_GROUNDING_REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)
_DOMAIN_TITLE = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+$", re.IGNORECASE)


def _normalized_host(host: str) -> str:
    return host.casefold().removeprefix("www.")


def _same_site(host: str, owned_domain: str) -> bool:
    """Exact domain or a subdomain of it; never a look-alike such as notintercom.com."""

    return bool(host) and (host == owned_domain or host.endswith("." + owned_domain))


def _citation_domain(citation: GeoCitation) -> str:
    host = _normalized_host(urlparse(str(citation.url)).hostname or "")
    # Gemini/Vertex grounding URIs are redirects; the source domain is in the title.
    if host.endswith(_GROUNDING_REDIRECT_HOSTS) and _DOMAIN_TITLE.match(citation.title.strip()):
        return _normalized_host(citation.title.strip())
    return host


def _name_pattern(name: str) -> re.Pattern[str]:
    # Short names are matched with their own casing so "Ada" is not "ADA compliance".
    flags = 0 if len(name) <= 4 else re.IGNORECASE
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", flags)


def _mentions(text: str, brand: str) -> bool:
    return _name_pattern(brand).search(text) is not None


def _mention_rank(text: str, brand: str, competitors: list[str]) -> int:
    positions: list[tuple[int, str]] = []
    for name in [brand, *competitors]:
        match = _name_pattern(name).search(text)
        if match:
            positions.append((match.start(), name.casefold()))
    ordered = [name for _, name in sorted(positions)]
    return ordered.index(brand.casefold()) + 1 if brand.casefold() in ordered else 1


def _account_label(provider: str, account_id: str) -> str:
    normalized = account_id.replace("act_", "")
    return f"{provider} · ••••{normalized[-4:]}"


def _demo_feedback(provider: ConnectorProvider, brand: str) -> list[FeedbackSignal]:
    if provider is ConnectorProvider.REDDIT:
        return [
            FeedbackSignal(
                source=FeedbackSource.REDDIT,
                text=f"{brand} is powerful, but onboarding is confusing for a small team.",
                external_id="synthetic-reddit-1",
            )
        ]
    return [
        FeedbackSignal(
            source=FeedbackSource.X,
            text=f"{brand} automation is helpful and fast once setup is complete.",
            external_id="synthetic-x-1",
        )
    ]
