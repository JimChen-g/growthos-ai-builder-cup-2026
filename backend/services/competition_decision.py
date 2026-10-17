"""Evidence validation, persistent quotas, caching and simulated approval execution."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from backend.config import Settings
from backend.models.competition import (
    CompetitionAnalysisRequest,
    CompetitionDecision,
    DecisionAction,
    DecisionSource,
    VertexDecisionPayload,
)
from backend.services.competition_store import (
    CompetitionStore,
    FirestoreCompetitionStore,
    InMemoryCompetitionStore,
)
from integrations.vertex_decision import VertexDecisionConfig, VertexDecisionGenerator


class DecisionGenerator(Protocol):
    async def analyze(self, request: CompetitionAnalysisRequest) -> VertexDecisionPayload: ...


class CompetitionQuotaExceededError(RuntimeError):
    pass


class CompetitionDecisionService:
    def __init__(
        self,
        *,
        store: CompetitionStore,
        generator: DecisionGenerator | None,
        model_id: str | None,
        session_limit: int,
        global_limit: int,
        cache_ttl_seconds: int,
        max_evidence_items: int,
    ) -> None:
        self.store = store
        self.generator = generator
        self.model_id = model_id
        self.session_limit = session_limit
        self.global_limit = global_limit
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_evidence_items = max_evidence_items

    async def analyze(
        self, session_id: str, request: CompetitionAnalysisRequest
    ) -> CompetitionDecision:
        if len(request.evidence) > self.max_evidence_items:
            raise ValueError(f"at most {self.max_evidence_items} evidence items are allowed")
        cache_key = request.cache_key()
        cached = await self.store.get_cache(cache_key, self.cache_ttl_seconds)
        failure_message: str | None = None
        if cached:
            payload = VertexDecisionPayload.model_validate(cached["payload"])
            source = DecisionSource.CACHE
            generated_at = datetime.fromisoformat(cached["generated_at"])
            model_id = cached.get("model_id")
            is_cached = True
        else:
            allowed = await self.store.reserve_quota(
                session_id,
                session_limit=self.session_limit,
                global_limit=self.global_limit,
            )
            if not allowed:
                raise CompetitionQuotaExceededError(
                    "The public demo AI limit has been reached. Try again after the UTC reset."
                )
            generated_at = datetime.now(UTC)
            model_id = self.model_id
            is_cached = False
            if self.generator is None:
                payload = self._fallback(request)
                source = DecisionSource.DETERMINISTIC_FALLBACK
                failure_message = "Live Vertex AI is not configured; a labeled fallback was used."
            else:
                try:
                    payload = await self.generator.analyze(request)
                    self._validate_evidence_references(payload, request)
                    source = DecisionSource.LIVE_VERTEX
                    await self.store.put_cache(
                        cache_key,
                        {
                            "payload": payload.model_dump(mode="json"),
                            "generated_at": generated_at.isoformat(),
                            "model_id": model_id,
                        },
                    )
                except Exception:  # noqa: BLE001 - response remains explicit about degradation
                    payload = self._fallback(request)
                    source = DecisionSource.DETERMINISTIC_FALLBACK
                    failure_message = (
                        "Live Vertex AI failed or returned invalid evidence references; "
                        "a labeled fallback was used."
                    )

        self._validate_evidence_references(payload, request)
        workflow_id = str(uuid4())
        action_digest = self._action_digest(payload.alternatives[0])
        decision = CompetitionDecision(
            workflow_id=workflow_id,
            **payload.model_dump(),
            evidence=request.evidence,
            source=source,
            model_id=model_id,
            cached=is_cached,
            generated_at=generated_at,
            action_digest=action_digest,
            failure_message=failure_message,
        )
        await self.store.create_workflow(session_id, decision.model_dump(mode="json"))
        return decision

    async def get(self, session_id: str, workflow_id: str) -> CompetitionDecision:
        value = await self.store.get_workflow(session_id, workflow_id)
        if value is None:
            raise KeyError("workflow was not found in this session")
        return self._from_store(value)

    async def decide(
        self,
        session_id: str,
        workflow_id: str,
        *,
        outcome: str,
        action_digest: str,
        actor: str,
        reason: str | None,
    ) -> CompetitionDecision:
        claimed = await self.store.decide_and_claim(
            session_id,
            workflow_id,
            outcome=outcome,
            action_digest=action_digest,
            actor=actor,
            reason=reason,
        )
        if (
            outcome == "reject"
            or claimed["approval_status"] != "executing"
            or not claimed.get("_claim_acquired", False)
        ):
            return self._from_store(claimed)
        # The competition environment never writes to an advertising account. The claimed action
        # is completed outside the transaction and persisted as an explicitly simulated result.
        result = {
            "kind": "simulated_execution",
            "status": "completed",
            "message": "Approved action recorded in the sandbox; no external account was changed.",
        }
        finished = await self.store.finish_execution(
            session_id,
            workflow_id,
            result=result,
            failure_message=None,
        )
        return self._from_store(finished)

    @staticmethod
    def _validate_evidence_references(
        payload: VertexDecisionPayload, request: CompetitionAnalysisRequest
    ) -> None:
        allowed = {item.evidence_id for item in request.evidence}
        cited = set(payload.priority_evidence_ids)
        cited.update(
            evidence_id for action in payload.alternatives for evidence_id in action.evidence_ids
        )
        invalid = sorted(cited - allowed)
        if invalid:
            raise ValueError(f"decision cited unknown evidence ids: {', '.join(invalid)}")
        if payload.alternatives[0].execution_kind != "simulated_campaign":
            raise ValueError("the first action must be the approval-gated simulated action")
        if payload.alternatives[1].execution_kind != "manual_recommendation":
            raise ValueError("the second action must remain a manual recommendation")

    @staticmethod
    def _fallback(request: CompetitionAnalysisRequest) -> VertexDecisionPayload:
        primary, secondary = request.evidence[:2]
        return VertexDecisionPayload(
            priority_problem=f"Review the operating signal: {primary.label}",
            priority_reason=(
                "The live model result is unavailable, so this fallback only prioritizes the "
                "first supplied evidence item and does not infer a cause."
            ),
            priority_evidence_ids=[primary.evidence_id],
            alternatives=[
                DecisionAction(
                    title=f"Run a bounded experiment for {primary.label}",
                    rationale="Use the cited sample fact as the baseline and require approval.",
                    evidence_ids=[primary.evidence_id],
                    observation_metric=f"Re-check {primary.label} after the observation window",
                    execution_kind="simulated_campaign",
                ),
                DecisionAction(
                    title=f"Investigate {secondary.label}",
                    rationale="Collect more context before turning this signal into an action.",
                    evidence_ids=[secondary.evidence_id],
                    observation_metric=f"Document a verified update for {secondary.label}",
                    execution_kind="manual_recommendation",
                ),
            ],
            missing_information=["Live model analysis is unavailable for this request."],
        )

    @staticmethod
    def _action_digest(action: DecisionAction) -> str:
        canonical = json.dumps(
            action.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _from_store(value: dict) -> CompetitionDecision:
        fields = CompetitionDecision.model_fields
        return CompetitionDecision.model_validate(
            {name: value[name] for name in fields if name in value}
        )


def build_competition_decision_service(settings: Settings) -> CompetitionDecisionService:
    project_id = settings.firebase_project_id or settings.vertex_ai_project_id
    if settings.competition_mode:
        if not project_id:
            raise RuntimeError("competition mode requires a Firebase or Vertex project id")
        store: CompetitionStore = FirestoreCompetitionStore(
            project_id,
            settings.firestore_database,
            settings.competition_data_retention_days,
        )
    else:
        store = InMemoryCompetitionStore()
    generator: DecisionGenerator | None = None
    if settings.vertex_ai_project_id:
        generator = VertexDecisionGenerator(
            VertexDecisionConfig(
                project_id=settings.vertex_ai_project_id,
                location=settings.vertex_ai_location,
                model=settings.vertex_ai_model,
                api_base_url=settings.vertex_ai_api_base_url,
                request_timeout_seconds=settings.vertex_ai_request_timeout_seconds,
            )
        )
    return CompetitionDecisionService(
        store=store,
        generator=generator,
        model_id=settings.vertex_ai_model if generator else None,
        session_limit=settings.competition_session_daily_limit,
        global_limit=settings.competition_global_daily_limit,
        cache_ttl_seconds=settings.competition_cache_ttl_seconds,
        max_evidence_items=settings.competition_max_evidence_items,
    )
