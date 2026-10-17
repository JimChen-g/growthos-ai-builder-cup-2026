"""Hash-proof contracts for the optional Phase 4.1 trust experiment."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.connectors import DataMode


class AuditBatchProof(BaseModel):
    """A deterministic, privacy-preserving proof of one ledger snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    batch_id: str = Field(min_length=1, max_length=100)
    mode: DataMode
    root_hash: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    previous_root: str = Field(default="0x" + "0" * 64, pattern=r"^0x[0-9a-f]{64}$")
    record_count: int = Field(ge=1, le=100)
    leaf_hashes: list[str] = Field(min_length=1, max_length=100)
    algorithm: Literal["sha256-merkle-v1"] = "sha256-merkle-v1"
    created_at: datetime


class AnchoredAuditBatch(BaseModel):
    """Public receipt returned after a proof is written to Injective Testnet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    network: Literal["injective-evm-testnet"] = "injective-evm-testnet"
    chain_id: Literal[1439] = 1439
    contract_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    deployment_transaction: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    anchor_transaction: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    observed_at_block: int = Field(ge=1)
    explorer_url: str
    proof: AuditBatchProof


class AuditIntegrityState(StrEnum):
    """Founder-facing state of one mode-isolated ledger proof.

    ``verified`` means the records covered by the receipt still hash to the anchored root;
    newer records may exist and are reported separately as pending. ``mismatch`` means the
    anchored records themselves changed or disappeared, which is an integrity failure.
    """

    VERIFIED = "verified"
    NOT_ANCHORED = "not_anchored"
    EMPTY = "empty"
    MISMATCH = "mismatch"


class PublicAuditAnchorReceipt(BaseModel):
    """Non-secret receipt safe to ship with the Demo workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    network: Literal["injective-evm-testnet"] = "injective-evm-testnet"
    chain_id: Literal[1439] = 1439
    mode: DataMode
    batch_id: str = Field(min_length=1, max_length=100)
    root_hash: str = Field(pattern=r"^0x[0-9a-f]{64}$")
    record_count: int = Field(ge=1, le=100)
    contract_address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    transaction_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    block_number: int = Field(ge=1)
    explorer_url: str
    verified_at: datetime


class AuditIntegrityStatus(BaseModel):
    """A local ledger-to-public-receipt comparison for the Founder Dashboard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DataMode
    state: AuditIntegrityState
    record_count: int = Field(ge=0)
    pending_record_count: int = Field(default=0, ge=0)
    batch_id: str | None = None
    root_hash: str | None = Field(default=None, pattern=r"^0x[0-9a-f]{64}$")
    network: str | None = None
    chain_id: int | None = None
    contract_address: str | None = None
    transaction_hash: str | None = None
    block_number: int | None = None
    explorer_url: str | None = None
    verified_at: datetime | None = None
