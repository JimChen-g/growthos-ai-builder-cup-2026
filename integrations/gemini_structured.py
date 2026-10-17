"""Gemini structured-output client implementing the StructuredGenerator boundary.

Used only for narration (briefing text, the agent's reply). Scores, evidence and actions
stay deterministic; every response is validated against a Pydantic schema by the caller,
and callers fall back to deterministic text when Gemini is unavailable.
"""

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from integrations.base import IntegrationAPIError, request_json


class GeminiStructuredConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr
    model: str = "gemini-2.5-flash"
    api_base_url: HttpUrl = "https://generativelanguage.googleapis.com/v1beta"
    temperature: float = Field(default=0.2, ge=0, le=1)
    request_timeout_seconds: float = Field(default=20.0, gt=0, le=120)


class GeminiStructuredGenerator:
    """Call ``models/{model}:generateContent`` with a JSON schema and parse the JSON reply."""

    provider = "gemini"

    def __init__(
        self, config: GeminiStructuredConfig, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_seconds)

    async def generate_json(
        self, *, prompt: str, response_schema: dict[str, Any]
    ) -> dict[str, Any]:
        url = (
            f"{str(self.config.api_base_url).rstrip('/')}/models/"
            f"{self.config.model}:generateContent"
        )
        payload = await request_json(
            self._client,
            self.provider,
            "POST",
            url,
            headers={
                "x-goog-api-key": self.config.api_key.get_secret_value(),
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": self.config.temperature,
                    "responseMimeType": "application/json",
                    "responseJsonSchema": response_schema,
                },
            },
        )
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
            result = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise IntegrationAPIError(
                self.provider, None, "structured response could not be parsed"
            ) from error
        if not isinstance(result, dict):
            raise IntegrationAPIError(self.provider, None, "structured response is not an object")
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def build_narration_generator(settings: Any) -> GeminiStructuredGenerator | None:
    """Enable configured narration only for deployments allowed to call live providers."""

    if getattr(settings, "public_demo_only", False) or not getattr(
        settings, "gemini_narration_enabled", False
    ):
        return None
    api_key = getattr(settings, "gemini_api_key", None)
    if not api_key:
        return None
    return GeminiStructuredGenerator(
        GeminiStructuredConfig(
            api_key=api_key,
            model=settings.gemini_narration_model,
            api_base_url=settings.gemini_api_base_url,
        )
    )
