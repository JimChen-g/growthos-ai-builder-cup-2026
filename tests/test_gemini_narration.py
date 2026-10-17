import json

import httpx
import pytest

from agents.founder_intent import analyze_founder_message
from backend.config import Settings
from backend.services.phase2_runtime import build_demo_runtime
from integrations.gemini_structured import (
    GeminiStructuredConfig,
    GeminiStructuredGenerator,
    build_narration_generator,
)


@pytest.mark.asyncio
async def test_gemini_generator_sends_json_schema_and_parses_reply() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        reply = {"acknowledgement": "Churn is not in the connected data yet."}
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]}
        )

    generator = GeminiStructuredGenerator(
        GeminiStructuredConfig(api_key="test-key"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    schema = {"type": "object", "properties": {"acknowledgement": {"type": "string"}}}
    result = await generator.generate_json(prompt="hello", response_schema=schema)
    await generator.aclose()

    assert result == {"acknowledgement": "Churn is not in the connected data yet."}
    assert seen["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert seen["key"] == "test-key"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert seen["body"]["generationConfig"]["responseJsonSchema"] == schema


def test_narration_is_opt_in() -> None:
    assert build_narration_generator(Settings(_env_file=None, gemini_api_key="k")) is None
    enabled = Settings(_env_file=None, gemini_api_key="k", gemini_narration_enabled=True)
    assert isinstance(build_narration_generator(enabled), GeminiStructuredGenerator)


def test_public_demo_only_disables_narration_even_when_configured() -> None:
    settings = Settings(
        _env_file=None,
        public_demo_only=True,
        gemini_api_key="test-key",
        gemini_narration_enabled=True,
    )
    assert build_narration_generator(settings) is None


class RecordingNarrator:
    def __init__(self, reply: dict | Exception) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def generate_json(self, *, prompt: str, response_schema: dict) -> dict:
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.asyncio
async def test_orchestrator_uses_gemini_reply_grounded_in_the_founder_message() -> None:
    narrator = RecordingNarrator({"acknowledgement": "Churn data isn't connected yet."})
    runtime = build_demo_runtime(narrator=narrator)
    run = await runtime.orchestrator.start(
        runtime.demo_request.model_copy(update={"founder_message": "Our churn doubled."})
    )
    assert run.narrative.acknowledgement == "Churn data isn't connected yet."
    assert "Our churn doubled." in narrator.prompts[0]
    assert "retention" in narrator.prompts[0]


@pytest.mark.asyncio
async def test_orchestrator_falls_back_when_gemini_fails() -> None:
    runtime = build_demo_runtime(narrator=RecordingNarrator(RuntimeError("quota")))
    run = await runtime.orchestrator.start(
        runtime.demo_request.model_copy(update={"founder_message": "Our churn doubled."})
    )
    assert run.narrative.acknowledgement.startswith("You raised churn/retention")


@pytest.mark.asyncio
async def test_founder_message_shapes_the_reply_without_gemini() -> None:
    runtime = build_demo_runtime()
    churn = await runtime.orchestrator.start(
        runtime.demo_request.model_copy(
            update={"founder_message": "Our churn doubled after the pricing change."}
        )
    )
    visibility = await runtime.orchestrator.start(
        runtime.demo_request.model_copy(
            update={"founder_message": "Nobody finds us when they ask ChatGPT."}
        )
    )
    assert "can't diagnose it reliably" in churn.narrative.acknowledgement
    assert visibility.narrative.recommended_actions[0].startswith("Publish")
    assert analyze_founder_message("CAC is rising and AI visibility is low").topics == (
        "acquisition",
        "visibility",
    )


def test_founder_message_in_chinese_maps_to_topics() -> None:
    assert analyze_founder_message("获客成本在上升，AI 可见度很低").topics == (
        "acquisition",
        "visibility",
    )
    assert analyze_founder_message("定價調整後，客戶流失翻倍了").topics == ("pricing", "retention")
