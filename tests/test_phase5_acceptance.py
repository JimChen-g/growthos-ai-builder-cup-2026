from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from backend.api.app import create_app
from backend.services.demo import load_demo_script
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import REPOSITORY_ROOT, build_demo_runtime


def test_phase5_fixture_freezes_case_and_three_minute_rundown() -> None:
    script = load_demo_script(REPOSITORY_ROOT / "data/demo_phase5_polish.json")

    assert script.fixed_case is True
    assert script.total_duration_seconds == 180
    assert script.case.title == "AI Customer Support Startup"
    assert script.case.team_size == 5
    assert script.case.mrr == Decimal("30000")
    assert script.case.growth_problems == ["CAC rising", "AI visibility low"]
    assert [source.label for source in script.data_sources] == [
        "Stripe",
        "Google Ads",
        "Meta Ads",
    ]
    assert [problem.title for problem in script.diagnosis.problems] == [
        "CAC rising",
        "AI visibility low",
    ]
    assert [action.title for action in script.actions] == [
        "Create Campaign",
        "GEO Plan",
    ]
    assert [concept.title for concept in script.future] == [
        "Growth Passport",
        "Growth Capital",
    ]
    assert [
        (stage.id.value, stage.start_seconds, stage.end_seconds) for stage in script.stages
    ] == [
        ("problem", 0, 30),
        ("connect_data", 30, 60),
        ("diagnosis", 60, 120),
        ("execution", 120, 160),
        ("future", 160, 180),
    ]


def test_phase5_contract_rejects_timeline_drift() -> None:
    script = load_demo_script(REPOSITORY_ROOT / "data/demo_phase5_polish.json")
    payload = script.model_dump(mode="json")
    payload["stages"][1]["end_seconds"] = 61

    with pytest.raises(ValidationError, match="fixed three-minute rundown"):
        type(script).model_validate(payload)


@pytest.mark.asyncio
async def test_phase5_api_is_stable_and_has_no_agent_side_effect() -> None:
    runtime = build_demo_runtime()
    founder_experience = build_demo_founder_experience(runtime)
    app = create_app(runtime, founder_experience)

    before = await runtime.audit_log.list()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/demo")
        second = await client.get("/api/v1/demo")
        health = await client.get("/health")
    after = await runtime.audit_log.list()

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["script_id"] == "growthos-phase5-fixed-demo"
    assert health.json() == {"status": "ok", "phase": "5"}
    assert before.total == after.total == 0
