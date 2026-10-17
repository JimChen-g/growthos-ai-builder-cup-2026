"""Bounded Vertex Gemini client for evidence-based competition decisions."""

import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from backend.models.competition import (
    CompetitionAnalysisRequest,
    VertexDecisionPayload,
)
from integrations.base import IntegrationAPIError
from integrations.geo_providers import AccessTokenProvider, GoogleADCAccessTokenProvider


class VertexDecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1)
    location: str = "global"
    model: str = "gemini-3.7-flash"
    api_base_url: HttpUrl = "https://aiplatform.googleapis.com"
    request_timeout_seconds: float = Field(default=40.0, gt=0, le=55)
    max_attempts: int = Field(default=2, ge=1, le=3)


class VertexDecisionGenerator:
    provider = "vertex_ai"

    def __init__(
        self,
        config: VertexDecisionConfig,
        *,
        client: httpx.AsyncClient | None = None,
        token_provider: AccessTokenProvider | None = None,
    ) -> None:
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_seconds)
        self._token_provider = token_provider or GoogleADCAccessTokenProvider()

    async def analyze(self, request: CompetitionAnalysisRequest) -> VertexDecisionPayload:
        token = await self._token_provider.access_token()
        evidence = [item.model_dump(mode="json") for item in request.evidence]
        prompt = (
            "You are GrowthOS, an enterprise operations decision analyst. Use only the supplied "
            "evidence. Rank the single most important problem and propose exactly two distinct "
            "actions. Cite only evidence_id values that appear in the input. Do not restate, "
            "convert, estimate, or invent numeric facts. The first action must use execution_kind "
            "simulated_campaign and may enter a human approval workflow. The second must use "
            "manual_recommendation. Say what information is missing instead of guessing.\n\n"
            f"Founder goal: {request.founder_goal}\n"
            f"Evidence JSON: {json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}"
        )
        url = (
            f"{str(self.config.api_base_url).rstrip('/')}/v1/projects/"
            f"{self.config.project_id}/locations/{self.config.location}/publishers/google/models/"
            f"{self.config.model}:generateContent"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": VertexDecisionPayload.model_json_schema(),
                "thinkingConfig": {"thinkingLevel": "LOW"},
                "maxOutputTokens": 2048,
            },
        }
        response: httpx.Response | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                response = await self._client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            except httpx.TimeoutException as error:
                if attempt == self.config.max_attempts:
                    raise IntegrationAPIError(
                        self.provider, None, "Vertex decision request timed out"
                    ) from error
                await asyncio.sleep(0.25 * attempt)
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            if attempt < self.config.max_attempts:
                await asyncio.sleep(0.25 * attempt)

        assert response is not None
        if response.status_code >= 400:
            raise IntegrationAPIError(
                self.provider,
                response.status_code,
                "Vertex decision request failed",
            )
        try:
            payload: dict[str, Any] = response.json()
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
            return VertexDecisionPayload.model_validate_json(text)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise IntegrationAPIError(
                self.provider, None, "Vertex structured decision could not be validated"
            ) from error

    async def aclose(self) -> None:
        await self._client.aclose()
