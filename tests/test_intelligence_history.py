from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from backend.config import Settings
from backend.models.audit_log import AgentActionStatus
from backend.models.connectors import DataMode, FeedbackCollectionRequest, GeoScanRequest
from backend.models.intelligence_history import IntelligenceKind
from backend.services.audit_log import AuditLogService
from backend.services.intelligence_history import IntelligenceHistoryService
from backend.services.real_intelligence import build_real_intelligence_runtime


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        connection_vault_key=SecretStr(Fernet.generate_key().decode()),
        connection_vault_path=tmp_path / "credentials.enc",
        demo_intelligence_history_path=tmp_path / "demo-history.json",
        live_intelligence_history_path=tmp_path / "live-history.json",
    )


@pytest.mark.asyncio
async def test_saved_results_survive_service_recreation_and_modes_are_isolated(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    runtime = build_real_intelligence_runtime(settings)
    request = GeoScanRequest(
        brand="Intercom",
        website="https://www.intercom.com",
        category="AI customer support",
        competitors=["Zendesk", "Ada"],
    )
    report = await runtime.geo.scan("local-demo", request)
    feedback = await runtime.feedback.collect(
        "local-demo",
        FeedbackCollectionRequest(brand="Intercom", mode=DataMode.DEMO),
    )

    demo_store = IntelligenceHistoryService(DataMode.DEMO, settings.demo_intelligence_history_path)
    await demo_store.record_geo(report)
    await demo_store.record_feedback(feedback)

    reopened = IntelligenceHistoryService(DataMode.DEMO, settings.demo_intelligence_history_path)
    live_store = IntelligenceHistoryService(DataMode.LIVE, settings.live_intelligence_history_path)
    demo_history = await reopened.list_snapshots(tenant_id="local-demo")
    live_history = await live_store.list_snapshots(tenant_id="local-demo")

    assert demo_history.total == 2
    assert {item.snapshot.kind for item in demo_history.items} == {
        IntelligenceKind.BRAND_VISIBILITY,
        IntelligenceKind.CUSTOMER_INTELLIGENCE,
    }
    assert demo_history.items[0].snapshot.result
    assert live_history.total == 0


@pytest.mark.asyncio
async def test_history_compares_runs_and_links_intervening_executed_actions(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    runtime = build_real_intelligence_runtime(settings)
    report = await runtime.geo.scan(
        "local-demo",
        GeoScanRequest(
            brand="Intercom",
            website="https://www.intercom.com",
            category="AI customer support",
            competitors=["Zendesk", "Ada"],
        ),
    )
    baseline_time = datetime(2026, 8, 17, 1, tzinfo=UTC)
    baseline = report.model_copy(
        update={
            "overall_visibility_score": Decimal("40"),
            "generated_at": baseline_time,
        }
    )
    improved = report.model_copy(
        update={
            "overall_visibility_score": Decimal("55"),
            "generated_at": baseline_time + timedelta(hours=2),
        }
    )
    store = IntelligenceHistoryService(DataMode.DEMO, settings.demo_intelligence_history_path)
    audit = AuditLogService(mode=DataMode.DEMO)

    await store.record_geo(baseline)
    action = await audit.record(
        agent="GEO Agent",
        action="Published an evidence-backed comparison page",
        reason="Improve AI search presence",
        status=AgentActionStatus.EXECUTED,
        timestamp=baseline_time + timedelta(hours=1),
    )
    await store.record_geo(improved)
    ledger = await audit.list()
    history = await store.list_snapshots(
        tenant_id="local-demo",
        kind=IntelligenceKind.BRAND_VISIBILITY,
        audit_entries=ledger.entries,
    )

    latest = history.items[0]
    assert history.total == 2
    assert latest.score_delta == Decimal("15")
    assert latest.trend == "improved"
    assert latest.observed_after_actions[0].entry_id == action.entry_id
    assert history.top_observed_effects[0].action == action.action
    assert history.top_observed_effects[0].observed_score_delta == Decimal("15")
