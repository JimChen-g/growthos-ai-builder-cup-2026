"""Grounded OpenAI, Gemini, Vertex AI, and Perplexity GEO provider adapters."""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from backend.models.connectors import ConnectorProvider, GeoCitation
from integrations.base import IntegrationAPIError, request_json


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    answer: str
    citations: list[GeoCitation]


class GeoProviderClient(Protocol):
    provider: ConnectorProvider

    async def ask(self, prompt: str) -> ProviderAnswer: ...

    async def aclose(self) -> None: ...


class OpenAIWebSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    model: str = "gpt-5.6-luna"
    api_base_url: HttpUrl = "https://api.openai.com/v1"
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=120)


class GeminiSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    model: str = "gemini-3.6-flash"
    api_base_url: HttpUrl = "https://generativelanguage.googleapis.com/v1beta"
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=120)


class VertexAISearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model: str = "gemini-2.5-flash"
    api_base_url: HttpUrl = "https://aiplatform.googleapis.com"
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=120)


class PerplexitySonarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    model: str = "sonar"
    api_base_url: HttpUrl = "https://api.perplexity.ai"
    request_timeout_seconds: float = Field(default=45.0, gt=0, le=120)


class _OwnedClient:
    def __init__(self, client: httpx.AsyncClient | None, timeout: float) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class AccessTokenProvider(Protocol):
    async def access_token(self) -> str: ...


class GoogleADCAccessTokenProvider:
    """Resolve short-lived Google OAuth tokens through Application Default Credentials."""

    def __init__(self) -> None:
        self._credentials: Any = None
        self._request: Any = None
        self._lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._sync_access_token)

    def _sync_access_token(self) -> str:
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError, RefreshError
            from google.auth.transport.requests import Request
        except ImportError as error:
            raise IntegrationAPIError(
                ConnectorProvider.GEMINI_VERTEX,
                None,
                "ADC authentication requires the google-auth package",
            ) from error
        try:
            if self._credentials is None:
                self._credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                self._request = Request()
            if not self._credentials.valid:
                self._credentials.refresh(self._request)
        except (DefaultCredentialsError, RefreshError) as error:
            raise IntegrationAPIError(
                ConnectorProvider.GEMINI_VERTEX,
                None,
                "Application Default Credentials are unavailable or could not be refreshed",
            ) from error
        token = self._credentials.token
        if not token:
            raise IntegrationAPIError(
                ConnectorProvider.GEMINI_VERTEX,
                None,
                "Application Default Credentials did not return an access token",
            )
        return str(token)


class OpenAIWebSearchProvider(_OwnedClient):
    """Use Responses API web search and preserve URL citation annotations."""

    provider = ConnectorProvider.OPENAI

    def __init__(
        self,
        config: OpenAIWebSearchConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client, config.request_timeout_seconds)
        self.config = config

    async def ask(self, prompt: str) -> ProviderAnswer:
        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            f"{str(self.config.api_base_url).rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "input": prompt,
                "tools": [{"type": "web_search", "search_context_size": "low"}],
                "tool_choice": "required",
                "reasoning": {"effort": "low"},
                "text": {"verbosity": "low"},
                "max_output_tokens": 1600,
            },
        )
        if not isinstance(payload, dict):
            raise IntegrationAPIError(self.provider, None, "invalid response payload")

        text_parts: list[str] = []
        citations: list[GeoCitation] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict) or content.get("type") not in {
                    "output_text",
                    "text",
                }:
                    continue
                if content.get("text"):
                    text_parts.append(str(content["text"]))
                citations.extend(_url_citations(content.get("annotations", [])))
        answer = "\n".join(text_parts).strip() or str(payload.get("output_text", "")).strip()
        if not answer:
            raise IntegrationAPIError(
                self.provider,
                None,
                _openai_missing_text_message(payload),
            )
        return ProviderAnswer(answer=answer, citations=_dedupe_citations(citations))


class GeminiSearchProvider(_OwnedClient):
    """Use the Gemini Developer API with grounded Google Search."""

    provider = ConnectorProvider.GEMINI

    def __init__(
        self,
        config: GeminiSearchConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client, config.request_timeout_seconds)
        self.config = config

    async def ask(self, prompt: str) -> ProviderAnswer:
        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            f"{str(self.config.api_base_url).rstrip('/')}/interactions",
            headers={
                "x-goog-api-key": self.config.api_key.get_secret_value(),
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "input": prompt,
                "tools": [{"type": "google_search"}],
            },
        )
        if not isinstance(payload, dict):
            raise IntegrationAPIError(self.provider, None, "invalid response payload")

        text_parts: list[str] = []
        citations: list[GeoCitation] = []
        steps = payload.get("steps") or payload.get("outputs") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("type") == "model_output":
                blocks = step.get("content", [])
            elif step.get("type") == "text":
                blocks = [step]
            else:
                continue
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") not in {"text", "output_text"}:
                    continue
                if block.get("text"):
                    text_parts.append(str(block["text"]))
                citations.extend(_url_citations(block.get("annotations", [])))
        answer = "\n".join(text_parts).strip() or str(payload.get("output_text", "")).strip()
        if not answer:
            raise IntegrationAPIError(self.provider, None, "response did not contain text")
        return ProviderAnswer(answer=answer, citations=_dedupe_citations(citations))


class VertexAISearchProvider(_OwnedClient):
    """Use Gemini on Vertex AI with Google Search grounding metadata."""

    provider = ConnectorProvider.GEMINI_VERTEX

    def __init__(
        self,
        config: VertexAISearchConfig,
        *,
        client: httpx.AsyncClient | None = None,
        token_provider: AccessTokenProvider | None = None,
    ) -> None:
        super().__init__(client, config.request_timeout_seconds)
        self.config = config
        self._token_provider = token_provider or GoogleADCAccessTokenProvider()

    async def ask(self, prompt: str) -> ProviderAnswer:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["x-goog-api-key"] = self.config.api_key.get_secret_value()
        else:
            headers["Authorization"] = f"Bearer {await self._token_provider.access_token()}"

        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            self._endpoint_url(),
            headers=headers,
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "tools": [{"googleSearch": {}}],
                "generationConfig": {"maxOutputTokens": 800},
            },
        )
        if not isinstance(payload, dict):
            raise IntegrationAPIError(self.provider, None, "invalid response payload")

        candidates = payload.get("candidates", [])
        if not candidates or not isinstance(candidates[0], dict):
            raise IntegrationAPIError(self.provider, None, "response did not contain a candidate")
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []
        answer = "\n".join(
            str(part["text"]) for part in parts if isinstance(part, dict) and part.get("text")
        ).strip()
        if not answer:
            raise IntegrationAPIError(self.provider, None, "response did not contain text")

        metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
        citations = _vertex_citations(metadata)
        return ProviderAnswer(answer=answer, citations=_dedupe_citations(citations))

    def _endpoint_url(self) -> str:
        base = str(self.config.api_base_url).rstrip("/")
        if base == "https://aiplatform.googleapis.com" and self.config.location != "global":
            base = f"https://{self.config.location}-aiplatform.googleapis.com"
        project = quote(self.config.project_id, safe="")
        location = quote(self.config.location, safe="")
        model = quote(self.config.model, safe="")
        return (
            f"{base}/v1/projects/{project}/locations/{location}/publishers/google/"
            f"models/{model}:generateContent"
        )


class PerplexitySonarProvider(_OwnedClient):
    """Use the grounded Sonar API and its explicit citations/search results."""

    provider = ConnectorProvider.PERPLEXITY

    def __init__(
        self,
        config: PerplexitySonarConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client, config.request_timeout_seconds)
        self.config = config

    async def ask(self, prompt: str) -> ProviderAnswer:
        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            f"{str(self.config.api_base_url).rstrip('/')}/v1/sonar",
            headers={
                "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "web_search_options": {"search_context_size": "low"},
            },
        )
        if not isinstance(payload, dict):
            raise IntegrationAPIError(self.provider, None, "invalid response payload")
        choices = payload.get("choices", [])
        answer = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            if isinstance(message, dict):
                answer = str(message.get("content", "")).strip()
        if not answer:
            raise IntegrationAPIError(self.provider, None, "response did not contain text")

        search_results = payload.get("search_results", [])
        titles = {
            str(item.get("url")): str(item.get("title", "Source"))
            for item in search_results
            if isinstance(item, dict) and item.get("url")
        }
        citations = [
            GeoCitation(title=titles.get(str(url), "Source"), url=url)
            for url in payload.get("citations", [])
            if isinstance(url, str) and url.startswith(("http://", "https://"))
        ]
        return ProviderAnswer(answer=answer, citations=_dedupe_citations(citations))


def _url_citations(annotations: Any) -> list[GeoCitation]:
    results: list[GeoCitation] = []
    if not isinstance(annotations, list):
        return results
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        url = annotation.get("url") or annotation.get("source")
        if annotation.get("type") not in {None, "url_citation"} or not isinstance(url, str):
            continue
        if not url.startswith(("http://", "https://")):
            continue
        results.append(GeoCitation(title=str(annotation.get("title") or "Source"), url=url))
    return results


def _openai_missing_text_message(payload: dict[str, Any]) -> str:
    """Turn a textless Responses payload into a useful, secret-free error."""

    if payload.get("status") != "incomplete":
        return "response did not contain text"
    details = payload.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, dict) else None
    if reason == "max_output_tokens":
        return "response stopped before the answer because the output limit was reached"
    return "response stopped before producing a complete answer"


def _vertex_citations(metadata: Any) -> list[GeoCitation]:
    if not isinstance(metadata, dict):
        return []
    chunks = metadata.get("groundingChunks") or metadata.get("grounding_chunks") or []
    results: list[GeoCitation] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        source = chunk.get("web") or chunk.get("retrievedContext") or {}
        if not isinstance(source, dict):
            continue
        url = source.get("uri") or source.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        results.append(GeoCitation(title=str(source.get("title") or "Source"), url=url))
    return results


def _dedupe_citations(citations: list[GeoCitation]) -> list[GeoCitation]:
    unique: dict[str, GeoCitation] = {}
    for citation in citations:
        unique.setdefault(str(citation.url), citation)
    return list(unique.values())
