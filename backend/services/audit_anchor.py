"""Deterministic hashing for privacy-preserving audit-ledger proofs."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.models.audit_anchor import (
    AuditBatchProof,
    AuditIntegrityState,
    AuditIntegrityStatus,
    PublicAuditAnchorReceipt,
)
from backend.models.audit_log import AgentActionLedgerEntry
from backend.models.connectors import DataMode

_LEAF_DOMAIN = b"growthos:audit-entry:v1\x00"
_NODE_DOMAIN = b"growthos:audit-node:v1\x00"
_ZERO_ROOT = "0x" + "0" * 64


def _canonical_timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_entry(entry: AgentActionLedgerEntry) -> bytes:
    """Serialize only the frozen ledger contract using stable JSON rules."""

    payload = {
        "action": entry.action,
        "agent": entry.agent,
        "approval_id": entry.approval_id,
        "entry_id": entry.entry_id,
        "mode": entry.mode.value,
        "reason": entry.reason,
        "schema_version": 1,
        "status": entry.status.value,
        "timestamp": _canonical_timestamp(entry.timestamp),
        "workflow_run_id": entry.workflow_run_id,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def audit_leaf_hash(entry: AgentActionLedgerEntry) -> bytes:
    """Hash one canonical record with a domain separator."""

    return hashlib.sha256(_LEAF_DOMAIN + canonical_entry(entry)).digest()


def merkle_root(leaves: list[bytes]) -> bytes:
    """Build a deterministic binary Merkle tree, duplicating an odd final node."""

    if not leaves:
        raise ValueError("At least one ledger entry is required to build an audit proof.")
    if any(len(leaf) != 32 for leaf in leaves):
        raise ValueError("Every Merkle leaf must be exactly 32 bytes.")

    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(_NODE_DOMAIN + level[index] + level[index + 1]).digest()
            for index in range(0, len(level), 2)
        ]
    return level[0]


def build_audit_batch_proof(
    entries: list[AgentActionLedgerEntry],
    *,
    mode: DataMode,
    previous_root: str = _ZERO_ROOT,
    created_at: datetime | None = None,
) -> AuditBatchProof:
    """Create a stable proof for an isolated Demo or Live ledger snapshot."""

    if not entries:
        raise ValueError("The selected ledger has no records to anchor.")
    if any(entry.mode is not mode for entry in entries):
        raise ValueError("A proof cannot mix Demo and Live ledger records.")

    ordered = sorted(
        entries,
        key=lambda entry: (_canonical_timestamp(entry.timestamp), entry.entry_id),
    )
    leaves = [audit_leaf_hash(entry) for entry in ordered]
    root = "0x" + merkle_root(leaves).hex()
    return AuditBatchProof(
        batch_id=f"growthos-{mode.value}-{root[2:18]}",
        mode=mode,
        root_hash=root,
        previous_root=previous_root,
        record_count=len(ordered),
        leaf_hashes=["0x" + leaf.hex() for leaf in leaves],
        created_at=created_at or datetime.now(UTC),
    )


def load_public_anchor_receipt(path: Path) -> PublicAuditAnchorReceipt | None:
    """Load a non-secret receipt when one exists for the selected workspace."""

    if not path.is_file():
        return None
    return PublicAuditAnchorReceipt.model_validate_json(path.read_text(encoding="utf-8"))


def build_audit_integrity_status(
    entries: list[AgentActionLedgerEntry],
    *,
    mode: DataMode,
    receipt: PublicAuditAnchorReceipt | None,
) -> AuditIntegrityStatus:
    """Compare one isolated ledger with only that workspace's public receipt."""

    if not entries:
        return AuditIntegrityStatus(
            mode=mode,
            state=AuditIntegrityState.EMPTY,
            record_count=0,
        )

    ordered = sorted(
        entries,
        key=lambda entry: (_canonical_timestamp(entry.timestamp), entry.entry_id),
    )
    if receipt is None:
        local_proof = build_audit_batch_proof(ordered, mode=mode) if len(ordered) <= 100 else None
        return AuditIntegrityStatus(
            mode=mode,
            state=AuditIntegrityState.NOT_ANCHORED,
            record_count=len(ordered),
            batch_id=local_proof.batch_id if local_proof else None,
            root_hash=local_proof.root_hash if local_proof else None,
        )

    # A receipt covers the first `record_count` records in canonical order. Records added
    # later are legitimate new evidence awaiting the next proof, not tampering.
    anchored_count = receipt.record_count
    if receipt.mode is not mode or len(ordered) < anchored_count:
        return AuditIntegrityStatus(
            mode=mode,
            state=AuditIntegrityState.MISMATCH,
            record_count=min(len(ordered), anchored_count),
            pending_record_count=max(0, len(ordered) - anchored_count),
        )
    proof = build_audit_batch_proof(ordered[:anchored_count], mode=mode)
    local = {
        "mode": mode,
        "record_count": proof.record_count,
        "pending_record_count": len(ordered) - anchored_count,
        "batch_id": proof.batch_id,
        "root_hash": proof.root_hash,
    }
    if receipt.batch_id != proof.batch_id or receipt.root_hash != proof.root_hash:
        return AuditIntegrityStatus(
            **local,
            state=AuditIntegrityState.MISMATCH,
        )
    return AuditIntegrityStatus(
        **local,
        state=AuditIntegrityState.VERIFIED,
        network=receipt.network,
        chain_id=receipt.chain_id,
        contract_address=receipt.contract_address,
        transaction_hash=receipt.transaction_hash,
        block_number=receipt.block_number,
        explorer_url=receipt.explorer_url,
        verified_at=receipt.verified_at,
    )
