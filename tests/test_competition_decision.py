import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from backend.api.security import workspace_id_for_uid
from backend.models.competition import (
    CompetitionAnalysisRequest,
    CompetitionDecision,
    DecisionAction,
    EvidenceItem,
    VertexDecisionPayload,
)
from backend.services.competition_decision import (
    CompetitionDecisionService,
    CompetitionQuotaExceededError,
)
from backend.services.competition_store import InMemoryCompetitionStore
from integrations.vertex_decision import VertexDecisionConfig, VertexDecisionGenerator


class CountingStore(InMemoryCompetitionStore):
    def __init__(self) -> None:
        super().__init__()
        self.execution_finishes = 0

    async def finish_execution(self, *args, **kwargs):
        self.execution_finishes += 1
        await asyncio.sleep(0)
        return await super().finish_execution(*args, **kwargs)


def request() -> CompetitionAnalysisRequest:
    return CompetitionAnalysisRequest(
        founder_goal="Choose the next action without inventing facts.",
        evidence=[
            EvidenceItem(
                evidence_id="EV-CAC",
                label="CAC",
                value="$120, +24%",
                source="synthetic ads snapshot",
                observed_at=datetime(2026, 10, 15, tzinfo=UTC),
            ),
            EvidenceItem(
                evidence_id="EV-ROAS",
                label="ROAS",
                value="2.8x",
                source="synthetic ads snapshot",
                observed_at=datetime(2026, 10, 15, tzinfo=UTC),
            ),
        ],
    )


def payload(evidence_id: str = "EV-CAC") -> VertexDecisionPayload:
    return VertexDecisionPayload(
        priority_problem="Acquisition efficiency needs review",
        priority_reason="The cited CAC signal changed.",
        priority_evidence_ids=[evidence_id],
        alternatives=[
            DecisionAction(
                title="Run a bounded campaign experiment",
                rationale="Test one reviewed change.",
                evidence_ids=[evidence_id],
                observation_metric="CAC after seven days",
                execution_kind="simulated_campaign",
            ),
            DecisionAction(
                title="Audit the funnel",
                rationale="Check the measurement inputs.",
                evidence_ids=["EV-ROAS"],
                observation_metric="Verified funnel report",
                execution_kind="manual_recommendation",
            ),
        ],
        missing_information=["Conversion quality by cohort"],
    )


class FakeGenerator:
    def __init__(self, result: VertexDecisionPayload) -> None:
        self.result = result
        self.calls = 0

    async def analyze(self, body: CompetitionAnalysisRequest) -> VertexDecisionPayload:
        self.calls += 1
        return self.result


def service(generator: FakeGenerator | None, *, store=None, session_limit: int = 20):
    return CompetitionDecisionService(
        store=store or InMemoryCompetitionStore(),
        generator=generator,
        model_id="gemini-3.7-flash",
        session_limit=session_limit,
        global_limit=500,
        cache_ttl_seconds=3600,
        max_evidence_items=30,
    )


@pytest.mark.asyncio
async def test_live_decision_is_evidence_bound_and_cached() -> None:
    generator = FakeGenerator(payload())
    selected = service(generator)

    first = await selected.analyze("session-a", request())
    second = await selected.analyze("session-a", request())

    assert first.source == "live_vertex"
    assert first.model_id == "gemini-3.7-flash"
    assert second.source == "cache"
    assert second.cached is True
    assert generator.calls == 1
    assert {item.evidence_id for item in first.evidence} == {"EV-CAC", "EV-ROAS"}


@pytest.mark.asyncio
async def test_unknown_model_citation_becomes_explicit_fallback() -> None:
    selected = service(FakeGenerator(payload("EV-INVENTED")))

    result = await selected.analyze("session-a", request())

    assert result.source == "deterministic_fallback"
    assert "invalid evidence references" in (result.failure_message or "")
    assert set(result.priority_evidence_ids) <= {"EV-CAC", "EV-ROAS"}


@pytest.mark.asyncio
async def test_approval_is_session_isolated_and_idempotent() -> None:
    generator = FakeGenerator(payload())
    selected = service(generator)
    decision = await selected.analyze("session-a", request())

    with pytest.raises(KeyError):
        await selected.get("session-b", decision.workflow_id)

    executed = await selected.decide(
        "session-a",
        decision.workflow_id,
        outcome="approve",
        action_digest=decision.action_digest,
        actor="anonymous:test",
        reason="reviewed",
    )
    repeated = await selected.decide(
        "session-a",
        decision.workflow_id,
        outcome="approve",
        action_digest=decision.action_digest,
        actor="anonymous:test",
        reason="reviewed",
    )

    assert executed.approval_status == "executed"
    assert repeated.approval_status == "executed"
    assert repeated.execution_result == executed.execution_result
    assert repeated.execution_result["kind"] == "simulated_execution"


@pytest.mark.asyncio
async def test_concurrent_approvals_only_one_request_executes() -> None:
    store = CountingStore()
    selected = service(FakeGenerator(payload()), store=store)
    decision = await selected.analyze("session-a", request())

    async def approve():
        return await selected.decide(
            "session-a",
            decision.workflow_id,
            outcome="approve",
            action_digest=decision.action_digest,
            actor="anonymous:test",
            reason="reviewed",
        )

    await asyncio.gather(approve(), approve())

    assert store.execution_finishes == 1
    final = await selected.get("session-a", decision.workflow_id)
    assert final.approval_status == "executed"


@pytest.mark.asyncio
async def test_quota_survives_service_recreation_with_shared_store() -> None:
    store = InMemoryCompetitionStore()
    first = service(FakeGenerator(payload()), store=store, session_limit=1)
    second = service(FakeGenerator(payload()), store=store, session_limit=1)

    await first.analyze("session-a", request())
    uncached = request().model_copy(update={"founder_goal": "A different uncached goal"})
    with pytest.raises(CompetitionQuotaExceededError):
        await second.analyze("session-a", uncached)


def test_workspace_id_is_stable_and_does_not_expose_firebase_uid() -> None:
    uid = "firebase-user-secret-identifier"
    workspace = workspace_id_for_uid(uid)

    assert workspace == workspace_id_for_uid(uid)
    assert uid not in workspace
    assert len(workspace) == 32


def test_analysis_language_defaults_to_english_and_rejects_unsupported_values() -> None:
    original = request().model_dump(exclude={"language"})

    assert CompetitionAnalysisRequest.model_validate(original).language == "en"
    with pytest.raises(ValidationError):
        CompetitionAnalysisRequest.model_validate({**original, "language": "zh"})


@pytest.mark.asyncio
async def test_decision_cache_is_separated_by_language_with_identical_evidence() -> None:
    generator = FakeGenerator(payload())
    selected = service(generator)
    original = request()

    for language in ("en", "zh-Hans", "zh-Hant"):
        localized = original.model_copy(update={"language": language})
        first = await selected.analyze("session-a", localized)
        cached = await selected.analyze("session-a", localized)

        assert first.source == "live_vertex"
        assert cached.source == "cache"
        assert first.language == cached.language == language
        assert first.evidence == cached.evidence == original.evidence

    assert generator.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "priority", "failure", "execution"),
    [
        ("zh-Hans", "审核证据 EV-CAC", "尚未配置", "已在模拟环境中记录批准的行动"),
        ("zh-Hant", "檢視證據 EV-CAC", "尚未設定", "已在模擬環境中記錄核准的行動"),
    ],
)
async def test_fallback_and_approval_retain_workflow_language_and_evidence(
    language: str, priority: str, failure: str, execution: str
) -> None:
    selected = service(None)
    original = request().model_copy(update={"language": language})
    decision = await selected.analyze("session-a", original)
    stored = await selected.get("session-a", decision.workflow_id)

    assert decision.language == stored.language == language
    assert decision.source == "deterministic_fallback"
    assert decision.priority_problem.startswith(priority)
    assert decision.failure_message.startswith(failure)
    assert decision.evidence == original.evidence
    assert [item.value for item in decision.evidence] == ["$120, +24%", "2.8x"]
    assert decision.alternatives[0].execution_kind == "simulated_campaign"
    assert decision.alternatives[1].execution_kind == "manual_recommendation"
    assert decision.action_digest == stored.action_digest

    approved = await selected.decide(
        "session-a",
        decision.workflow_id,
        outcome="approve",
        action_digest=decision.action_digest,
        actor="anonymous:test",
        reason=None,
    )
    assert approved.execution_result["message"].startswith(execution)
    assert approved.action_digest == decision.action_digest
    assert approved.alternatives == decision.alternatives


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected"),
    [("zh-Hans", "无效的证据引用"), ("zh-Hant", "無效的證據引用")],
)
async def test_model_failure_is_localized(language: str, expected: str) -> None:
    selected = service(FakeGenerator(payload("EV-INVENTED")))
    result = await selected.analyze(
        "session-a", request().model_copy(update={"language": language})
    )

    assert result.source == "deterministic_fallback"
    assert expected in result.failure_message


@pytest.mark.asyncio
async def test_existing_workflow_without_language_remains_readable() -> None:
    selected = service(FakeGenerator(payload()))
    decision = await selected.analyze("session-a", request())
    legacy = decision.model_dump(mode="json", exclude={"language"})

    assert CompetitionDecision.model_validate(legacy).language == "en"
    assert selected._from_store(legacy).action_digest == decision.action_digest


class StaticTokenProvider:
    async def access_token(self) -> str:
        return "test-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "English"),
        ("zh-Hans", "Simplified Chinese (简体中文); use simplified characters throughout"),
        ("zh-Hant", "Traditional Chinese (繁體中文); use traditional characters throughout"),
    ],
)
async def test_vertex_requests_selected_language_for_all_prose_fields(
    language: str, expected: str
) -> None:
    original = request().model_copy(update={"language": language})

    def handler(outbound: httpx.Request) -> httpx.Response:
        body = json.loads(outbound.content)
        instruction = body["systemInstruction"]["parts"][0]["text"]
        assert f"Write every prose response field in {expected}" in instruction
        for field in (
            "priority_problem",
            "priority_reason",
            "title",
            "rationale",
            "observation_metric",
            "missing_information",
        ):
            assert field in instruction
        assert "Retain proper names" in instruction
        assert "evidence_id references and execution_kind enum values unchanged" in instruction
        prompt = body["contents"][0]["parts"][0]["text"]
        evidence = json.loads(prompt.split("Evidence JSON: ", 1)[1])
        assert evidence == [item.model_dump(mode="json") for item in original.evidence]
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": payload().model_dump_json()}]}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        generator = VertexDecisionGenerator(
            VertexDecisionConfig(project_id="test-project"),
            client=client,
            token_provider=StaticTokenProvider(),
        )
        assert await generator.analyze(original) == payload()
