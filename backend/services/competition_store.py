"""Persistent state, cache, quotas and approval transactions for the public demo."""

import asyncio
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class CompetitionStore(Protocol):
    async def get_cache(self, cache_key: str, ttl_seconds: int) -> dict[str, Any] | None: ...

    async def put_cache(self, cache_key: str, value: dict[str, Any]) -> None: ...

    async def reserve_quota(
        self, session_id: str, *, session_limit: int, global_limit: int
    ) -> bool: ...

    async def create_workflow(self, session_id: str, value: dict[str, Any]) -> None: ...

    async def get_workflow(self, session_id: str, workflow_id: str) -> dict[str, Any] | None: ...

    async def decide_and_claim(
        self,
        session_id: str,
        workflow_id: str,
        *,
        outcome: str,
        action_digest: str,
        actor: str,
        reason: str | None,
    ) -> dict[str, Any]: ...

    async def finish_execution(
        self,
        session_id: str,
        workflow_id: str,
        *,
        result: dict[str, str] | None,
        failure_message: str | None,
    ) -> dict[str, Any]: ...


class InMemoryCompetitionStore:
    """Test/local implementation with the same atomic state transitions as Firestore."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._quota: dict[str, int] = defaultdict(int)
        self._workflows: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_cache(self, cache_key: str, ttl_seconds: int) -> dict[str, Any] | None:
        async with self._lock:
            cached = self._cache.get(cache_key)
            if not cached:
                return None
            age = (datetime.now(UTC) - datetime.fromisoformat(cached["cached_at"])).total_seconds()
            return deepcopy(cached["value"]) if age <= ttl_seconds else None

    async def put_cache(self, cache_key: str, value: dict[str, Any]) -> None:
        async with self._lock:
            self._cache[cache_key] = {
                "cached_at": datetime.now(UTC).isoformat(),
                "value": deepcopy(value),
            }

    async def reserve_quota(
        self, session_id: str, *, session_limit: int, global_limit: int
    ) -> bool:
        day = datetime.now(UTC).date().isoformat()
        session_key = f"session:{day}:{session_id}"
        global_key = f"global:{day}"
        async with self._lock:
            if self._quota[session_key] >= session_limit or self._quota[global_key] >= global_limit:
                return False
            self._quota[session_key] += 1
            self._quota[global_key] += 1
            return True

    async def create_workflow(self, session_id: str, value: dict[str, Any]) -> None:
        key = (session_id, value["workflow_id"])
        async with self._lock:
            if key in self._workflows:
                raise ValueError("workflow already exists")
            self._workflows[key] = deepcopy(value)

    async def get_workflow(self, session_id: str, workflow_id: str) -> dict[str, Any] | None:
        async with self._lock:
            value = self._workflows.get((session_id, workflow_id))
            return deepcopy(value) if value else None

    async def decide_and_claim(
        self,
        session_id: str,
        workflow_id: str,
        *,
        outcome: str,
        action_digest: str,
        actor: str,
        reason: str | None,
    ) -> dict[str, Any]:
        key = (session_id, workflow_id)
        async with self._lock:
            current = self._workflows.get(key)
            if not current:
                raise KeyError("workflow was not found in this session")
            if current["action_digest"] != action_digest:
                raise ValueError("the approved action does not match the reviewed action")
            status = current["approval_status"]
            if status != "pending":
                expected = (
                    "rejected"
                    if outcome == "reject"
                    else {"executing", "executed", "failed"}
                )
                if status == expected or (isinstance(expected, set) and status in expected):
                    return {**deepcopy(current), "_claim_acquired": False}
                raise ValueError(f"workflow is already {status}")
            current.update(
                {
                    "approval_status": "rejected" if outcome == "reject" else "executing",
                    "decided_by": actor,
                    "decision_reason": reason,
                    "decided_at": datetime.now(UTC).isoformat(),
                }
            )
            return {**deepcopy(current), "_claim_acquired": outcome == "approve"}

    async def finish_execution(
        self,
        session_id: str,
        workflow_id: str,
        *,
        result: dict[str, str] | None,
        failure_message: str | None,
    ) -> dict[str, Any]:
        key = (session_id, workflow_id)
        async with self._lock:
            current = self._workflows.get(key)
            if not current:
                raise KeyError("workflow was not found in this session")
            if current["approval_status"] in {"executed", "failed"}:
                return deepcopy(current)
            if current["approval_status"] != "executing":
                raise ValueError("workflow has not been claimed for execution")
            current.update(
                {
                    "approval_status": "failed" if failure_message else "executed",
                    "execution_result": result,
                    "failure_message": failure_message,
                    "executed_at": datetime.now(UTC).isoformat(),
                }
            )
            return deepcopy(current)


class FirestoreCompetitionStore:
    """Firestore implementation; all quota and approval transitions use transactions."""

    def __init__(
        self,
        project_id: str,
        database: str = "(default)",
        retention_days: int = 7,
    ) -> None:
        try:
            from google.cloud import firestore
        except ImportError as error:  # pragma: no cover - deployment dependency check
            raise RuntimeError("google-cloud-firestore is required for competition mode") from error
        self._firestore = firestore
        self._client = firestore.Client(project=project_id, database=database)
        self._retention_days = retention_days

    def _workflow_ref(self, session_id: str, workflow_id: str):
        return (
            self._client.collection("competition_sessions")
            .document(session_id)
            .collection("workflows")
            .document(workflow_id)
        )

    async def get_cache(self, cache_key: str, ttl_seconds: int) -> dict[str, Any] | None:
        def read() -> dict[str, Any] | None:
            snapshot = self._client.collection("competition_cache").document(cache_key).get()
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            age = (datetime.now(UTC) - datetime.fromisoformat(data["cached_at"])).total_seconds()
            return data["value"] if age <= ttl_seconds else None

        return await asyncio.to_thread(read)

    async def put_cache(self, cache_key: str, value: dict[str, Any]) -> None:
        await asyncio.to_thread(
            self._client.collection("competition_cache").document(cache_key).set,
            {
                "cached_at": datetime.now(UTC).isoformat(),
                "expires_at": datetime.now(UTC) + timedelta(days=self._retention_days),
                "value": value,
            },
        )

    async def reserve_quota(
        self, session_id: str, *, session_limit: int, global_limit: int
    ) -> bool:
        day = datetime.now(UTC).date().isoformat()
        global_ref = self._client.collection("competition_quota").document(f"global-{day}")
        session_ref = self._client.collection("competition_quota").document(
            f"session-{day}-{session_id}"
        )

        def reserve() -> bool:
            transaction = self._client.transaction()
            expires_at = datetime.now(UTC) + timedelta(days=self._retention_days)

            @self._firestore.transactional
            def apply(transaction):
                global_doc = global_ref.get(transaction=transaction)
                session_doc = session_ref.get(transaction=transaction)
                global_count = (global_doc.to_dict() or {}).get("count", 0)
                session_count = (session_doc.to_dict() or {}).get("count", 0)
                if global_count >= global_limit or session_count >= session_limit:
                    return False
                transaction.set(
                    global_ref,
                    {"count": global_count + 1, "day": day, "expires_at": expires_at},
                )
                transaction.set(
                    session_ref,
                    {
                        "count": session_count + 1,
                        "day": day,
                        "session_id": session_id,
                        "expires_at": expires_at,
                    },
                )
                return True

            return apply(transaction)

        return await asyncio.to_thread(reserve)

    async def create_workflow(self, session_id: str, value: dict[str, Any]) -> None:
        await asyncio.to_thread(
            self._workflow_ref(session_id, value["workflow_id"]).create,
            {
                **value,
                "expires_at": datetime.now(UTC) + timedelta(days=self._retention_days),
            },
        )

    async def get_workflow(self, session_id: str, workflow_id: str) -> dict[str, Any] | None:
        snapshot = await asyncio.to_thread(self._workflow_ref(session_id, workflow_id).get)
        return snapshot.to_dict() if snapshot.exists else None

    async def decide_and_claim(
        self,
        session_id: str,
        workflow_id: str,
        *,
        outcome: str,
        action_digest: str,
        actor: str,
        reason: str | None,
    ) -> dict[str, Any]:
        ref = self._workflow_ref(session_id, workflow_id)

        def decide() -> dict[str, Any]:
            transaction = self._client.transaction()

            @self._firestore.transactional
            def apply(transaction):
                snapshot = ref.get(transaction=transaction)
                if not snapshot.exists:
                    raise KeyError("workflow was not found in this session")
                current = snapshot.to_dict()
                if current["action_digest"] != action_digest:
                    raise ValueError("the approved action does not match the reviewed action")
                status = current["approval_status"]
                if status != "pending":
                    valid = status == "rejected" if outcome == "reject" else status in {
                        "executing",
                        "executed",
                        "failed",
                    }
                    if valid:
                        return {**current, "_claim_acquired": False}
                    raise ValueError(f"workflow is already {status}")
                update = {
                    "approval_status": "rejected" if outcome == "reject" else "executing",
                    "decided_by": actor,
                    "decision_reason": reason,
                    "decided_at": datetime.now(UTC).isoformat(),
                }
                transaction.update(ref, update)
                return {
                    **current,
                    **update,
                    "_claim_acquired": outcome == "approve",
                }

            return apply(transaction)

        return await asyncio.to_thread(decide)

    async def finish_execution(
        self,
        session_id: str,
        workflow_id: str,
        *,
        result: dict[str, str] | None,
        failure_message: str | None,
    ) -> dict[str, Any]:
        ref = self._workflow_ref(session_id, workflow_id)

        def finish() -> dict[str, Any]:
            transaction = self._client.transaction()

            @self._firestore.transactional
            def apply(transaction):
                snapshot = ref.get(transaction=transaction)
                if not snapshot.exists:
                    raise KeyError("workflow was not found in this session")
                current = snapshot.to_dict()
                if current["approval_status"] in {"executed", "failed"}:
                    return current
                if current["approval_status"] != "executing":
                    raise ValueError("workflow has not been claimed for execution")
                update = {
                    "approval_status": "failed" if failure_message else "executed",
                    "execution_result": result,
                    "failure_message": failure_message,
                    "executed_at": datetime.now(UTC).isoformat(),
                }
                transaction.update(ref, update)
                return {**current, **update}

            return apply(transaction)

        return await asyncio.to_thread(finish)
