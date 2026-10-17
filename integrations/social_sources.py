"""Read-only X Recent Search and Reddit web-search fallback adapters."""

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from backend.models.feedback import FeedbackSignal, FeedbackSource
from integrations.base import IntegrationAPIError, request_json


class XRecentSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bearer_token: SecretStr
    api_base_url: HttpUrl = "https://api.x.com/2"
    request_timeout_seconds: float = Field(default=12.0, gt=0, le=60)


class RedditSearchFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    perplexity_api_key: SecretStr
    api_base_url: HttpUrl = "https://api.perplexity.ai"
    request_timeout_seconds: float = Field(default=12.0, gt=0, le=60)


class _OwnedClient:
    def __init__(self, client: httpx.AsyncClient | None, timeout: float) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class XRecentSearch(_OwnedClient):
    provider = "x"

    def __init__(
        self,
        config: XRecentSearchConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client, config.request_timeout_seconds)
        self.config = config

    async def collect(self, brand: str, *, max_results: int = 10) -> list[FeedbackSignal]:
        payload = await request_json(
            self._client,
            self.provider,
            "GET",
            f"{str(self.config.api_base_url).rstrip('/')}/tweets/search/recent",
            headers={"Authorization": f"Bearer {self.config.bearer_token.get_secret_value()}"},
            params={
                "query": f'"{brand}" lang:en -is:retweet',
                "max_results": max(10, min(max_results, 100)),
                "tweet.fields": "created_at,lang",
            },
        )
        if not isinstance(payload, dict):
            raise IntegrationAPIError(self.provider, None, "invalid response payload")
        return [
            FeedbackSignal(
                source=FeedbackSource.X,
                text=str(item["text"]),
                external_id=str(item.get("id", "")) or None,
            )
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("text")
        ]


class RedditSearchFallback(_OwnedClient):
    """Search public Reddit URLs without scraping or broad Reddit Data API access."""

    provider = "reddit"

    def __init__(
        self,
        config: RedditSearchFallbackConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(client, config.request_timeout_seconds)
        self.config = config

    async def collect(self, brand: str, *, max_results: int = 10) -> list[FeedbackSignal]:
        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            f"{str(self.config.api_base_url).rstrip('/')}/search",
            headers={
                "Authorization": (f"Bearer {self.config.perplexity_api_key.get_secret_value()}"),
                "Content-Type": "application/json",
            },
            json={
                "query": f'"{brand}" reviews complaints customer feedback',
                "max_results": max(1, min(max_results, 20)),
                "search_domain_filter": ["reddit.com"],
                "search_recency_filter": "year",
            },
        )
        if not isinstance(payload, dict):
            raise IntegrationAPIError(self.provider, None, "invalid response payload")
        return _reddit_signals(payload.get("results", []))


def _reddit_signals(results: Any) -> list[FeedbackSignal]:
    if not isinstance(results, list):
        return []
    signals: list[FeedbackSignal] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        snippet = str(item.get("snippet") or "").strip()
        url = str(item.get("url") or "").strip()
        if len(snippet) < 3:
            continue
        signals.append(
            FeedbackSignal(
                source=FeedbackSource.REDDIT,
                text=snippet[:1000],
                external_id=url[:200] if url else None,
            )
        )
    return signals
