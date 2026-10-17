"""Phase 3.1 connection center, GEO runner, and live customer signals."""

import asyncio
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from agents.feedback_agent import FeedbackAgent
from backend.config import Settings
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
    MetaAdsConnectionRequest,
    ProviderVisibility,
    VertexAIConnectionRequest,
)
from backend.models.feedback import FeedbackSignal, FeedbackSource
from backend.services.connector_security import (
    CredentialVaultError,
    EncryptedCredentialVault,
    QuotaExceededError,
    TenantQuotaLimiter,
)
from integrations.base import IntegrationAPIError, IntegrationError
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
from integrations.social_sources import (
    RedditSearchFallback,
    RedditSearchFallbackConfig,
    XRecentSearch,
    XRecentSearchConfig,
)

_CREDENTIAL_PROVIDERS = {
    ConnectorProvider.OPENAI,
    ConnectorProvider.GEMINI,
    ConnectorProvider.PERPLEXITY,
    ConnectorProvider.X,
}


class UnsupportedConnectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedVertexAIConnection:
    project_id: str
    location: str
    api_key: SecretStr | None
    source: str


@dataclass(frozen=True, slots=True)
class ResolvedMetaAdsConnection:
    access_token: SecretStr
    ad_account_id: str
    api_version: str
    source: str


class ConnectorCredentialResolver:
    """Resolve tenant vault secrets; environment secrets belong only to local-demo."""

    def __init__(self, settings: Settings, vault: EncryptedCredentialVault) -> None:
        self.settings = settings
        self.vault = vault

    async def secret(
        self, tenant_id: str, provider: ConnectorProvider
    ) -> tuple[SecretStr | None, str | None]:
        stored = await self.vault.get(tenant_id, provider)
        if stored:
            return stored, "encrypted_vault"
        if tenant_id != "local-demo":
            return None, None
        environment = {
            ConnectorProvider.OPENAI: self.settings.openai_api_key,
            ConnectorProvider.GEMINI: self.settings.gemini_api_key,
            ConnectorProvider.PERPLEXITY: self.settings.perplexity_api_key,
            ConnectorProvider.X: self.settings.x_bearer_token,
        }.get(provider)
        return (environment, "server_environment") if environment else (None, None)

    async def vertex_connection(self, tenant_id: str) -> ResolvedVertexAIConnection | None:
        stored = await self.vault.get(tenant_id, ConnectorProvider.GEMINI_VERTEX)
        if stored:
            try:
                payload = json.loads(stored.get_secret_value())
                project_id = str(payload["project_id"])
                location = str(payload.get("location") or "global")
                api_key_value = payload.get("api_key")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise CredentialVaultError(
                    "stored Vertex AI connection configuration is invalid"
                ) from error
            return ResolvedVertexAIConnection(
                project_id=project_id,
                location=location,
                api_key=SecretStr(str(api_key_value)) if api_key_value else None,
                source="encrypted_vault",
            )
        if tenant_id != "local-demo" or not self.settings.vertex_ai_project_id:
            return None
        return ResolvedVertexAIConnection(
            project_id=self.settings.vertex_ai_project_id,
            location=self.settings.vertex_ai_location,
            api_key=self.settings.vertex_ai_api_key,
            source="server_environment",
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
                source="encrypted_vault",
            )
        if tenant_id != "local-demo" or not all(
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
            source="server_environment",
        )


class ConnectionCenterService:
    def __init__(
        self,
        settings: Settings,
        vault: EncryptedCredentialVault,
        resolver: ConnectorCredentialResolver,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.resolver = resolver
        self._selected_accounts: dict[tuple[str, ConnectorProvider], str] = {}

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
        return ConnectionCenter(
            tenant_id=tenant_id,
            vault_encryption_enabled=self.vault.enabled,
            connections=[
                *ai_statuses,
                reddit,
                self._google_ads_status(tenant_id),
                await self._meta_ads_status(tenant_id),
                self._stripe_status(tenant_id),
            ],
        )

    async def save_secret(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        secret: SecretStr,
    ) -> ConnectionStatus:
        if provider not in _CREDENTIAL_PROVIDERS:
            raise UnsupportedConnectionError(
                f"{provider.value} does not accept a direct credential in this center"
            )
        await self.vault.put(tenant_id, provider, secret)
        center = await self.center(tenant_id)
        return next(item for item in center.connections if item.provider is provider)

    async def save_vertex_connection(
        self,
        tenant_id: str,
        request: VertexAIConnectionRequest,
    ) -> ConnectionStatus:
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
        }
        await self.vault.put(
            tenant_id,
            ConnectorProvider.META_ADS,
            SecretStr(json.dumps(payload, separators=(",", ":"), sort_keys=True)),
        )
        self._selected_accounts[(tenant_id, ConnectorProvider.META_ADS)] = request.ad_account_id
        return await self._meta_ads_status(tenant_id)

    async def growth_settings(self, tenant_id: str) -> Settings:
        """Use the saved Meta connection while retaining other business-source settings."""

        connection = await self.resolver.meta_connection(tenant_id)
        updates = {
            "data_source_mode": "live",
            "meta_ads_access_token": None,
            "meta_ads_ad_account_id": None,
            "meta_ads_api_version": None,
        }
        if connection:
            updates.update(
                meta_ads_access_token=connection.access_token,
                meta_ads_ad_account_id=connection.ad_account_id,
                meta_ads_api_version=connection.api_version,
            )
        return self.settings.model_copy(update=updates)

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
                success = True
                message = "Live Meta Ads read-only Insights request completed successfully."
        except httpx.HTTPError:
            success = False
            message = "Meta could not be reached. Try again."
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
            self._google_ads_status(tenant_id)
            if provider is ConnectorProvider.GOOGLE_ADS
            else await self._meta_ads_status(tenant_id)
        )
        if account_id not in {item.account_id for item in status.accounts}:
            raise UnsupportedConnectionError("account is not available to this connection")
        self._selected_accounts[(tenant_id, provider)] = account_id
        return (
            self._google_ads_status(tenant_id)
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
        uses_api_key = bool(connection and connection.api_key)
        return ConnectionStatus(
            provider=ConnectorProvider.GEMINI_VERTEX,
            display_name="Gemini · Vertex AI",
            category="AI visibility",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method=(
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
                "Enter the project ID",
                "Use server ADC or paste one API key",
                "Test Google Search grounding",
            ],
            estimated_setup_minutes=5,
            note=(
                "Gemini on Vertex AI with Google Search grounding; global is the simplest "
                "default, while regional endpoints support data-location controls."
            ),
        )

    def _google_ads_status(self, tenant_id: str) -> ConnectionStatus:
        configured = self.settings.google_ads_has_credentials and tenant_id == "local-demo"
        account_id = self.settings.google_ads_customer_id if configured else None
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
        selected = self._selected_accounts.get(
            (tenant_id, ConnectorProvider.GOOGLE_ADS), account_id
        )
        return ConnectionStatus(
            provider=ConnectorProvider.GOOGLE_ADS,
            display_name="Google Ads",
            category="Business data",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method="Server OAuth refresh token + developer token",
            configuration_source="server_environment" if configured else None,
            selected_account_id=selected,
            accounts=accounts,
            setup_kind=ConnectionSetupKind.OAUTH,
            setup_label="Configured on the GrowthOS server",
            setup_steps=[
                "Create a Google Ads OAuth client and refresh token",
                "Add them with the developer token to the server .env",
                "Choose the Ads account here; writes stay approval-gated",
            ],
            estimated_setup_minutes=3,
            note="Read access is live; every campaign write remains founder-approval gated.",
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
        selected = self._selected_accounts.get((tenant_id, ConnectorProvider.META_ADS), account_id)
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

    def _stripe_status(self, tenant_id: str) -> ConnectionStatus:
        configured = self.settings.stripe_secret_key is not None and tenant_id == "local-demo"
        return ConnectionStatus(
            provider=ConnectorProvider.STRIPE,
            display_name="Stripe",
            category="Business data",
            state=(
                ConnectionState.CONNECTED if configured else ConnectionState.NEEDS_CONFIGURATION
            ),
            configured=configured,
            auth_method="Restricted secret key",
            configuration_source="server_environment" if configured else None,
            setup_kind=ConnectionSetupKind.RESTRICTED_KEY,
            setup_label="Configured on the GrowthOS server",
            setup_steps=[
                "Create a restricted Stripe key",
                "Allow read-only charges and subscriptions",
                "Add it as STRIPE_SECRET_KEY on the server",
            ],
            estimated_setup_minutes=2,
            note="Read-only revenue intelligence connection.",
        )


class GeoProviderFactory:
    def __init__(self, settings: Settings, resolver: ConnectorCredentialResolver) -> None:
        self.settings = settings
        self.resolver = resolver

    async def build(self, tenant_id: str, provider: ConnectorProvider) -> GeoProviderClient | None:
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
                    request_timeout_seconds=self.settings.geo_request_timeout_seconds,
                )
            )
        secret, _ = await self.resolver.secret(tenant_id, provider)
        if not secret:
            return None
        timeout = self.settings.geo_request_timeout_seconds
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

    def __init__(self, factory: GeoProviderFactory, quota: TenantQuotaLimiter) -> None:
        self.factory = factory
        self.quota = quota

    async def scan(self, tenant_id: str, request: GeoScanRequest) -> GeoScanReport:
        async def provider_results(provider: ConnectorProvider) -> list[GeoPromptResult]:
            if request.mode is DataMode.DEMO:
                return self._demo_results(provider, request)
            return await self._live_results(tenant_id, provider, request)

        # Providers run concurrently; prompts within one provider stay sequential.
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
            await self.quota.consume(tenant_id, provider)
            try:
                answer = await client.ask("Name one widely used customer support software product.")
            finally:
                await client.aclose()
        except (IntegrationError, QuotaExceededError, UnsupportedConnectionError) as error:
            return ConnectionTestResult(
                provider=provider,
                configured=True,
                checked_live=True,
                success=False,
                message=str(error),
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
    ) -> list[GeoPromptResult]:
        client = await self.factory.build(tenant_id, provider)
        if not client:
            return [
                _error_result(provider, prompt, "No credential is configured for live mode.")
                for prompt in request.prompts
            ]
        results: list[GeoPromptResult] = []
        try:
            for prompt in request.prompts:
                started = monotonic()
                try:
                    await self.quota.consume(tenant_id, provider)
                    raw = await client.ask(prompt)
                    results.append(
                        _measured_result(
                            provider,
                            prompt,
                            raw.answer,
                            raw.citations,
                            request.brand,
                            request.competitors,
                            str(request.website),
                            int((monotonic() - started) * 1000),
                        )
                    )
                except (IntegrationError, QuotaExceededError) as error:
                    results.append(
                        _error_result(
                            provider,
                            prompt,
                            str(error),
                            int((monotonic() - started) * 1000),
                        )
                    )
        finally:
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
            return [], method, str(error)
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
    vault = EncryptedCredentialVault(
        selected.connection_vault_path,
        selected.connection_vault_key,
    )
    resolver = ConnectorCredentialResolver(selected, vault)
    quota = TenantQuotaLimiter(selected.geo_daily_provider_quota)
    return RealIntelligenceRuntime(
        connections=ConnectionCenterService(selected, vault, resolver),
        geo=GeoPromptRunner(GeoProviderFactory(selected, resolver), quota),
        feedback=FeedbackCollector(selected, resolver, quota),
    )


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
        status = "completed"
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
