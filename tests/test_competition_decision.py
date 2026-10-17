import asyncio
from datetime import UTC, datetime

import pytest

from backend.api.security import workspace_id_for_uid
from backend.models.competition import (
    CompetitionAnalysisRequest,
    DecisionAction,
    EvidenceItem,
    VertexDecisionPayload,
)
from backend.services.competition_decision import (
    CompetitionDecisionService,
    CompetitionQuotaExceededError,
)
from backend.services.competition_store import InMemoryCompetitionStore


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


def service(generator: FakeGenerator, *, store=None, session_limit: int = 20):
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
