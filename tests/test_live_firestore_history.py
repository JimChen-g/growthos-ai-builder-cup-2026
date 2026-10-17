"""Hosted Live persistence contracts; fake Firestore shares state across service instances."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock

import pytest
from google.api_core.exceptions import AlreadyExists

from backend.models.audit_log import AgentActionStatus
from backend.models.connectors import DataMode, FeedbackCollectionResponse, GeoScanReport
from backend.models.intelligence_history import IntelligenceKind
from backend.services.intelligence_history import (
    FirestoreAuditLogService,
    FirestoreIntelligenceHistoryService,
)


class FakeDocumentSnapshot:
    def __init__(self, payload):
        self.exists = payload is not None
        self._payload = deepcopy(payload)

    def to_dict(self):
        return deepcopy(self._payload)


class FakeReference:
    def __init__(self, client, path):
        self.client = client
        self.path = path

    def collection(self, name):
        return FakeReference(self.client, (*self.path, name))

    def document(self, name):
        return FakeReference(self.client, (*self.path, name))

    def create(self, payload):
        with self.client.lock:
            if self.path in self.client.values:
                raise AlreadyExists("document exists")
            self.client.values[self.path] = deepcopy(payload)

    def get(self):
        with self.client.lock:
            return FakeDocumentSnapshot(self.client.values.get(self.path))

    def stream(self):
        with self.client.lock:
            return iter([
                FakeDocumentSnapshot(value)
                for path, value in self.client.values.items()
                if len(path) == len(self.path) + 1 and path[:-1] == self.path
            ])


class FakeFirestore:
    def __init__(self):
        self.values = {}
        self.lock = RLock()

    def collection(self, name):
        return FakeReference(self, (name,))


def history(client, workspace="live-a"):
    return FirestoreIntelligenceHistoryService("project", "(default)", workspace, client=client)


def audit(client, workspace="live-a"):
    return FirestoreAuditLogService("project", "(default)", workspace, client=client)


def geo(workspace="live-a", *, score="40", brand="Acme", at=None):
    return GeoScanReport(
        tenant_id=workspace,
        mode=DataMode.LIVE,
        brand=brand,
        website="https://example.com",
        overall_visibility_score=Decimal(score),
        providers=[],
        results=[],
        provenance="Live test response",
        generated_at=at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_firestore_history_survives_recreation_and_keeps_workspaces_isolated():
    client = FakeFirestore()
    report = geo()
    first = await history(client).record_geo(report)
    feedback = FeedbackCollectionResponse(
        tenant_id="live-a", mode=DataMode.LIVE, brand="Acme", sources=[], signals=[]
    )
    await history(client).record_feedback(feedback)

    reopened = await history(client).list_snapshots(tenant_id="live-a")
    other = await history(client, "live-b").list_snapshots(tenant_id="live-b")
    assert reopened.total == 2
    stored = next(
        item.snapshot for item in reopened.items if item.snapshot.snapshot_id == first.snapshot_id
    )
    assert stored.result == report.model_dump(mode="json")
    assert other.total == 0
    assert all(path[:2] == ("live_workspaces", "live-a") for path in client.values)
    assert all(
        value["expires_at"] > datetime.now(UTC) + timedelta(days=6)
        for value in client.values.values()
    )


@pytest.mark.asyncio
async def test_firestore_history_rejects_tenant_or_mode_confusion_before_writing():
    client = FakeFirestore()
    store = history(client)
    with pytest.raises(ValueError, match="authenticated workspace"):
        await store.record_geo(geo("live-b"))
    with pytest.raises(ValueError, match="authenticated workspace"):
        await store.list_snapshots(tenant_id="live-b")
    with pytest.raises(ValueError, match="only Live"):
        await store.record_geo(geo().model_copy(update={"mode": DataMode.DEMO}))
    assert client.values == {}


@pytest.mark.asyncio
async def test_firestore_history_preserves_comparisons_and_concurrent_appends():
    client = FakeFirestore()
    now = datetime.now(UTC)
    await asyncio.gather(
        history(client).record_geo(geo(score="40", at=now)),
        history(client).record_geo(geo(score="55", at=now + timedelta(hours=2))),
        history(client).record_geo(geo(score="90", brand="Other", at=now + timedelta(hours=1))),
    )
    action = await audit(client).record(
        agent="GEO Agent", action="Published a comparison page", reason="Improve discovery",
        status=AgentActionStatus.EXECUTED, timestamp=now + timedelta(hours=1),
    )
    result = await history(client).list_snapshots(
        tenant_id="live-a", kind=IntelligenceKind.BRAND_VISIBILITY,
        audit_entries=await audit(client).all_entries(),
    )
    assert result.total == 3
    latest = result.items[0]
    assert latest.snapshot.brand == "Acme"
    assert latest.score_delta == Decimal("15")
    assert latest.observed_after_actions[0].entry_id == action.entry_id
    assert result.items[1].score_delta is None


@pytest.mark.asyncio
async def test_expired_history_is_hidden_before_firestore_ttl_deletes_it():
    client = FakeFirestore()
    await history(client).record_geo(geo())
    for payload in client.values.values():
        payload["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert (await history(client).list_snapshots(tenant_id="live-a")).total == 0
    assert len(client.values) == 1  # reads do not delete data or change TTL ownership


@pytest.mark.asyncio
async def test_audit_append_is_idempotent_across_instances_without_overwriting_original():
    client = FakeFirestore()
    arguments = {
        "agent": "GEO Agent", "action": "Assessed live AI search visibility",
        "reason": "Measured Acme", "status": AgentActionStatus.EXECUTED,
        "idempotency_key": "scan-123",
    }
    records = await asyncio.gather(*(audit(client).record(**arguments) for _ in range(12)))
    assert len({record.entry_id for record in records}) == 1
    repeated = await audit(client).record(**{**arguments, "reason": "Must not replace original"})
    assert repeated.reason == "Measured Acme"
    assert (await audit(client).list()).total == 1
    assert len(client.values) == 1
    assert next(iter(client.values))[0:3] == ("live_workspaces", "live-a", "audit_log")


@pytest.mark.asyncio
async def test_audit_is_workspace_scoped_and_supports_filters_limits_and_expiry():
    client = FakeFirestore()
    now = datetime.now(UTC)
    for workspace, state, second in [
        ("live-a", AgentActionStatus.EXECUTED, 0),
        ("live-a", AgentActionStatus.FAILED, 1),
        ("live-b", AgentActionStatus.EXECUTED, 2),
    ]:
        await audit(client, workspace).record(
            agent="GEO Agent", action="Assessed visibility", reason=workspace,
            status=state, timestamp=now + timedelta(seconds=second),
            idempotency_key=f"event-{second}",
        )
    result = await audit(client).list(limit=1)
    assert result.total == 2
    assert result.entries[0].status is AgentActionStatus.FAILED
    assert (await audit(client).list(status=AgentActionStatus.EXECUTED)).total == 1
    assert [entry.reason for entry in await audit(client, "live-b").all_entries()] == ["live-b"]
    for path, payload in client.values.items():
        if path[1] == "live-a":
            payload["expires_at"] = now - timedelta(seconds=1)
    assert (await audit(client).list()).total == 0
    assert (await audit(client, "live-b").list()).total == 1


@pytest.mark.parametrize("workspace", ["", "../other", "a/b", "A", "x" * 65])
def test_workspace_store_rejects_invalid_document_paths(workspace):
    with pytest.raises(ValueError, match="workspace id"):
        history(FakeFirestore(), workspace)


@pytest.mark.asyncio
async def test_firestore_failures_propagate_instead_of_claiming_local_success():
    class UnavailableReference(FakeReference):
        def collection(self, name):
            return UnavailableReference(self.client, (*self.path, name))

        def document(self, name):
            return UnavailableReference(self.client, (*self.path, name))

        def create(self, payload):
            raise ConnectionError("Firestore is unavailable")

        def stream(self):
            raise ConnectionError("Firestore is unavailable")

    class UnavailableFirestore(FakeFirestore):
        def collection(self, name):
            return UnavailableReference(self, (name,))

    client = UnavailableFirestore()
    with pytest.raises(ConnectionError, match="unavailable"):
        await history(client).record_geo(geo())
    with pytest.raises(ConnectionError, match="unavailable"):
        await history(client).list_snapshots(tenant_id="live-a")
    with pytest.raises(ConnectionError, match="unavailable"):
        await audit(client).record(
            agent="GEO", action="Measure", reason="Acme", status=AgentActionStatus.EXECUTED
        )
    assert client.values == {}
