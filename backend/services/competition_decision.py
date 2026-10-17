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
    DecisionLanguage,
    DecisionSource,
    VertexDecisionPayload,
)
from backend.services.competition_store import (
    CompetitionStore,
    FirestoreCompetitionStore,
    InMemoryCompetitionStore,
)
from integrations.vertex_decision import VertexDecisionConfig, VertexDecisionGenerator

_MESSAGES: dict[DecisionLanguage, dict[str, str]] = {
    "en": {
        "quota": "The public demo AI limit has been reached. Try again after the UTC reset.",
        "unconfigured": "Live Vertex AI is not configured; a labeled fallback was used.",
        "failed": (
            "Live Vertex AI failed or returned invalid evidence references; "
            "a labeled fallback was used."
        ),
        "executed": "Approved action recorded in the sandbox; no external account was changed.",
        "priority": "Review the operating signal: {primary}",
        "reason": (
            "The live model result is unavailable, so this fallback only prioritizes the "
            "first supplied evidence item and does not infer a cause."
        ),
        "experiment": "Run a bounded experiment for {primary}",
        "experiment_reason": "Use the cited sample fact as the baseline and require approval.",
        "experiment_metric": "Re-check {primary} after the observation window",
        "investigation": "Investigate {secondary}",
        "investigation_reason": "Collect more context before turning this signal into an action.",
        "investigation_metric": "Document a verified update for {secondary}",
        "missing": "Live model analysis is unavailable for this request.",
    },
    "zh-Hans": {
        "quota": "公开体验的每日 AI 分析额度已用完，请在 UTC 零点额度重置后重试。",
        "unconfigured": "尚未配置 Vertex AI 实时分析，当前显示已标注的备用建议。",
        "failed": "Vertex AI 实时分析失败或返回了无效的证据引用，当前显示已标注的备用建议。",
        "executed": "已在模拟环境中记录批准的行动，未更改任何外部账户。",
        "priority": "审核证据 {primary} 反映的经营信号",
        "reason": "实时模型结果暂不可用。此备用建议仅优先展示第一条证据，不推断其原因。",
        "experiment": "针对证据 {primary} 开展小范围实验",
        "experiment_reason": "以引用的样例事实为基准，获得人工批准后再开展模拟实验。",
        "experiment_metric": "观察期结束后重新核实证据 {primary} 对应的指标",
        "investigation": "进一步核查证据 {secondary}",
        "investigation_reason": "收集更多背景信息后，再决定是否采取行动。",
        "investigation_metric": "记录证据 {secondary} 对应的已核实进展",
        "missing": "本次请求暂时无法获得实时模型分析。",
    },
    "zh-Hant": {
        "quota": "公開體驗的每日 AI 分析額度已用完，請在 UTC 零點額度重設後重試。",
        "unconfigured": "尚未設定 Vertex AI 即時分析，目前顯示已標示的備用建議。",
        "failed": "Vertex AI 即時分析失敗或傳回無效的證據引用，目前顯示已標示的備用建議。",
        "executed": "已在模擬環境中記錄核准的行動，未變更任何外部帳戶。",
        "priority": "檢視證據 {primary} 反映的營運訊號",
        "reason": "即時模型結果暫時無法取得。此備用建議僅優先呈現第一項證據，不推斷其原因。",
        "experiment": "針對證據 {primary} 進行小範圍實驗",
        "experiment_reason": "以引用的範例事實為基準，取得人工核准後再進行模擬實驗。",
        "experiment_metric": "觀察期結束後重新核實證據 {primary} 對應的指標",
        "investigation": "進一步查證證據 {secondary}",
        "investigation_reason": "蒐集更多背景資訊後，再決定是否採取行動。",
        "investigation_metric": "記錄證據 {secondary} 對應的已核實進展",
        "missing": "本次請求暫時無法取得即時模型分析。",
    },
}


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
        messages = _MESSAGES[request.language]
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
                raise CompetitionQuotaExceededError(messages["quota"])
            generated_at = datetime.now(UTC)
            model_id = self.model_id
            is_cached = False
            if self.generator is None:
                payload = self._fallback(request)
                source = DecisionSource.DETERMINISTIC_FALLBACK
                failure_message = messages["unconfigured"]
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
                    failure_message = messages["failed"]

        self._validate_evidence_references(payload, request)
        workflow_id = str(uuid4())
        action_digest = self._action_digest(payload.alternatives[0])
        decision = CompetitionDecision(
            workflow_id=workflow_id,
            **payload.model_dump(),
            evidence=request.evidence,
            source=source,
            model_id=model_id,
            language=request.language,
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
            "message": _MESSAGES[self._from_store(claimed).language]["executed"],
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
        messages = _MESSAGES[request.language]
        # Canonical evidence is immutable. Refer to its ID rather than copying an arbitrary
        # English label into Chinese prose or pretending to translate a user-supplied fact.
        names = {
            "primary": primary.label if request.language == "en" else primary.evidence_id,
            "secondary": secondary.label if request.language == "en" else secondary.evidence_id,
        }
        return VertexDecisionPayload(
            priority_problem=messages["priority"].format(**names),
            priority_reason=messages["reason"],
            priority_evidence_ids=[primary.evidence_id],
            alternatives=[
                DecisionAction(
                    title=messages["experiment"].format(**names),
                    rationale=messages["experiment_reason"],
                    evidence_ids=[primary.evidence_id],
                    observation_metric=messages["experiment_metric"].format(**names),
                    execution_kind="simulated_campaign",
                ),
                DecisionAction(
                    title=messages["investigation"].format(**names),
                    rationale=messages["investigation_reason"],
                    evidence_ids=[secondary.evidence_id],
                    observation_metric=messages["investigation_metric"].format(**names),
                    execution_kind="manual_recommendation",
                ),
            ],
            missing_information=[messages["missing"]],
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
