from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.api.app import create_app
from backend.models.audit_log import AgentActionLedgerEntry, AgentActionStatus
from backend.models.connectors import DataMode
from backend.services.audit_anchor import build_audit_batch_proof
from backend.services.audit_log import AuditLogService
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


def entry(*, entry_id: str, mode: DataMode, minute: int = 0) -> AgentActionLedgerEntry:
    return AgentActionLedgerEntry(
        entry_id=entry_id,
        mode=mode,
        agent="Ads Agent",
        action="Prepared campaign",
        reason="Improve CAC",
        timestamp=datetime(2026, 8, 16, 12, tzinfo=UTC) + timedelta(minutes=minute),
        status=AgentActionStatus.AWAITING_APPROVAL,
    )


def test_audit_proof_is_stable_across_input_order() -> None:
    first = entry(entry_id="entry-a", mode=DataMode.DEMO)
    second = entry(entry_id="entry-b", mode=DataMode.DEMO, minute=1)
    created_at = datetime(2026, 8, 16, 13, tzinfo=UTC)

    forward = build_audit_batch_proof([first, second], mode=DataMode.DEMO, created_at=created_at)
    reverse = build_audit_batch_proof([second, first], mode=DataMode.DEMO, created_at=created_at)

    assert forward.root_hash == reverse.root_hash
    assert forward.leaf_hashes == reverse.leaf_hashes
    assert forward.batch_id == reverse.batch_id
    assert forward.record_count == 2


def test_audit_proof_rejects_empty_and_mixed_mode_batches() -> None:
    with pytest.raises(ValueError, match="no records"):
        build_audit_batch_proof([], mode=DataMode.LIVE)
    with pytest.raises(ValueError, match="cannot mix"):
        build_audit_batch_proof(
            [entry(entry_id="live", mode=DataMode.LIVE)],
            mode=DataMode.DEMO,
        )


@pytest.mark.asyncio
async def test_demo_proof_api_does_not_populate_live_ledger() -> None:
    runtime = build_demo_runtime()
    live_audit_log = AuditLogService(mode=DataMode.LIVE)
    app = create_app(
        runtime=runtime,
        founder_experience=build_demo_founder_experience(runtime),
        live_audit_log=live_audit_log,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        demo = await client.get("/api/v1/audit-proof?mode=demo")
        live = await client.get("/api/v1/audit-log?mode=live")
        empty_live_proof = await client.get("/api/v1/audit-proof?mode=live")

    assert demo.status_code == 200
    assert demo.json()["mode"] == "demo"
    assert demo.json()["record_count"] == 3
    assert live.json() == {"mode": "live", "entries": [], "total": 0}
    assert empty_live_proof.status_code == 409


@pytest.mark.asyncio
async def test_demo_proof_survives_runtime_restarts() -> None:
    async def proof_from_fresh_runtime() -> dict:
        runtime = build_demo_runtime()
        app = create_app(
            runtime=runtime,
            founder_experience=build_demo_founder_experience(runtime),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/audit-proof?mode=demo")
        assert response.status_code == 200
        return response.json()

    first = await proof_from_fresh_runtime()
    second = await proof_from_fresh_runtime()

    assert first["root_hash"] == second["root_hash"]
    assert first["batch_id"] == second["batch_id"]
    assert first["leaf_hashes"] == second["leaf_hashes"]


@pytest.mark.asyncio
async def test_integrity_status_keeps_demo_and_live_proofs_separate() -> None:
    runtime = build_demo_runtime()
    live_audit_log = AuditLogService(mode=DataMode.LIVE)
    app = create_app(
        runtime=runtime,
        founder_experience=build_demo_founder_experience(runtime),
        live_audit_log=live_audit_log,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        demo = (await client.get("/api/v1/audit-proof/status?mode=demo")).json()
        empty_live = (await client.get("/api/v1/audit-proof/status?mode=live")).json()
        await live_audit_log.record(
            agent="Growth Intelligence Agent",
            action="Refreshed connected business metrics",
            reason="The founder requested a Live refresh.",
            status=AgentActionStatus.EXECUTED,
        )
        live = (await client.get("/api/v1/audit-proof/status?mode=live")).json()

    assert demo["state"] == "verified"
    assert demo["mode"] == "demo"
    assert demo["transaction_hash"].startswith("0x")
    assert empty_live == {
        "mode": "live",
        "state": "empty",
        "record_count": 0,
        "pending_record_count": 0,
        "batch_id": None,
        "root_hash": None,
        "network": None,
        "chain_id": None,
        "contract_address": None,
        "transaction_hash": None,
        "block_number": None,
        "explorer_url": None,
        "verified_at": None,
    }
    assert live["state"] == "not_anchored"
    assert live["mode"] == "live"
    assert live["transaction_hash"] is None
    assert live["root_hash"] != demo["root_hash"]
