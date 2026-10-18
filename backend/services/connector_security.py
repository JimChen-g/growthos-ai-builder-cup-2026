"""Encrypted, tenant-scoped connector credentials and daily call quotas."""

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from backend.models.connectors import ConnectorProvider


class VaultUnavailableError(RuntimeError):
    """The local encrypted credential vault has no configured master key."""


class CredentialVaultError(RuntimeError):
    """The encrypted vault is corrupt or cannot be decrypted with the configured key."""


class QuotaExceededError(RuntimeError):
    """A tenant exhausted its daily allowance for one provider."""


@dataclass(frozen=True)
class CredentialMetadata:
    """Non-secret lifecycle information; never a claim of provider verification."""

    storage_status: str
    saved_at: datetime | None = None
    expires_at: datetime | None = None


class CredentialVault(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def get(self, tenant_id: str, provider: ConnectorProvider) -> SecretStr | None: ...

    async def put(self, tenant_id: str, provider: ConnectorProvider, secret: SecretStr) -> None: ...

    async def delete(self, tenant_id: str, provider: ConnectorProvider) -> None: ...

    async def metadata(
        self, tenant_id: str, provider: ConnectorProvider
    ) -> CredentialMetadata: ...


class EncryptedCredentialVault:
    """Persist only encrypted connector secrets, partitioned by tenant and provider."""

    def __init__(self, path: Path, key: SecretStr | None) -> None:
        self.path = path
        self._fernet = Fernet(key.get_secret_value().encode()) if key else None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    async def get(self, tenant_id: str, provider: ConnectorProvider) -> SecretStr | None:
        if not self._fernet:
            return None
        async with self._lock:
            contents = self._read()
            value = contents.get(tenant_id, {}).get(provider.value)
            return SecretStr(value) if isinstance(value, str) and value else None

    async def metadata(
        self, tenant_id: str, provider: ConnectorProvider
    ) -> CredentialMetadata:
        if not self.enabled:
            return CredentialMetadata("unavailable")
        # Legacy local vaults do not store timestamps. Do not fabricate them or
        # migrate local credentials into the hosted account's private workspace.
        return CredentialMetadata("saved" if await self.get(tenant_id, provider) else "missing")

    async def put(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        secret: SecretStr,
    ) -> None:
        if not self._fernet:
            raise VaultUnavailableError(
                "encrypted credential storage is disabled; set CONNECTION_VAULT_KEY"
            )
        async with self._lock:
            contents = self._read()
            contents.setdefault(tenant_id, {})[provider.value] = secret.get_secret_value()
            self._write(contents)

    async def delete(self, tenant_id: str, provider: ConnectorProvider) -> None:
        if not self._fernet:
            raise VaultUnavailableError("encrypted credential storage is disabled")
        async with self._lock:
            contents = self._read()
            contents.get(tenant_id, {}).pop(provider.value, None)
            self._write(contents)

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            encrypted = self.path.read_bytes()
            assert self._fernet is not None
            payload = json.loads(self._fernet.decrypt(encrypted))
        except (InvalidToken, OSError, ValueError, AssertionError) as error:
            raise CredentialVaultError(
                "connector credential vault could not be decrypted"
            ) from error
        if not isinstance(payload, dict):
            raise CredentialVaultError("connector credential vault has an invalid payload")
        return payload

    def _write(self, contents: dict[str, dict[str, str]]) -> None:
        assert self._fernet is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        encrypted = self._fernet.encrypt(
            json.dumps(contents, separators=(",", ":"), sort_keys=True).encode()
        )
        temporary.write_bytes(encrypted)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


class TenantQuotaLimiter:
    """Small in-process V1 limiter keyed by tenant, provider, and UTC day."""

    def __init__(self, daily_limit: int) -> None:
        self.daily_limit = daily_limit
        self._usage: dict[tuple[str, ConnectorProvider, date], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def consume(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        *,
        amount: int = 1,
        at: datetime | None = None,
    ) -> None:
        day = (at or datetime.now(UTC)).date()
        key = (tenant_id, provider, day)
        async with self._lock:
            if self._usage[key] + amount > self.daily_limit:
                raise QuotaExceededError(
                    f"daily {provider.value} quota of {self.daily_limit} calls reached"
                )
            self._usage[key] += amount

    async def used(self, tenant_id: str, provider: ConnectorProvider) -> int:
        key = (tenant_id, provider, datetime.now(UTC).date())
        async with self._lock:
            return self._usage[key]


class FirestoreCredentialVault:
    """Encrypted tenant records shared across hosted instances; no local secret files."""

    def __init__(
        self,
        project_id: str,
        database: str,
        key: SecretStr | None,
        retention_days: int = 7,
        *,
        client=None,
    ) -> None:
        from google.cloud import firestore

        self._client = client or firestore.Client(project=project_id, database=database)
        self._fernet = Fernet(key.get_secret_value().encode()) if key else None
        self._retention_days = retention_days

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def _ref(self, tenant_id: str, provider: ConnectorProvider):
        return (
            self._client.collection("live_workspaces")
            .document(tenant_id)
            .collection("connections")
            .document(provider.value)
        )

    async def get(self, tenant_id: str, provider: ConnectorProvider) -> SecretStr | None:
        if not self._fernet:
            return None
        snapshot = await asyncio.to_thread(self._ref(tenant_id, provider).get)
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        if data.get("expires_at") and data["expires_at"] <= datetime.now(UTC):
            return None
        try:
            payload = json.loads(self._fernet.decrypt(data["ciphertext"].encode()))
            if payload["tenant_id"] != tenant_id or payload["provider"] != provider.value:
                raise ValueError("credential context mismatch")
            secret = payload["secret"]
            if not isinstance(secret, str) or not secret:
                raise ValueError("invalid credential")
            return SecretStr(secret)
        except (KeyError, TypeError, ValueError, InvalidToken) as error:
            raise CredentialVaultError(
                "stored connector credential could not be decrypted"
            ) from error

    async def metadata(
        self, tenant_id: str, provider: ConnectorProvider
    ) -> CredentialMetadata:
        if not self.enabled:
            return CredentialMetadata("unavailable")
        snapshot = await asyncio.to_thread(self._ref(tenant_id, provider).get)
        if not snapshot.exists:
            return CredentialMetadata("missing")
        data = snapshot.to_dict() or {}
        saved_at = data.get("updated_at")
        expires_at = data.get("expires_at")
        # Return only validated lifecycle fields, not ciphertext or plaintext.
        saved_at = saved_at if isinstance(saved_at, datetime) else None
        expires_at = expires_at if isinstance(expires_at, datetime) else None
        if saved_at and saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=UTC)
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        status = "expired" if expires_at and expires_at <= datetime.now(UTC) else "saved"
        return CredentialMetadata(status, saved_at, expires_at)

    async def put(self, tenant_id: str, provider: ConnectorProvider, secret: SecretStr) -> None:
        if not self._fernet:
            raise VaultUnavailableError("encrypted credential storage is disabled")
        payload = json.dumps(
            {
                "tenant_id": tenant_id,
                "provider": provider.value,
                "secret": secret.get_secret_value(),
            },
            separators=(",", ":"),
        )
        await asyncio.to_thread(
            self._ref(tenant_id, provider).set,
            {
                "ciphertext": self._fernet.encrypt(payload.encode()).decode(),
                "updated_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(days=self._retention_days),
            },
        )

    async def delete(self, tenant_id: str, provider: ConnectorProvider) -> None:
        await asyncio.to_thread(self._ref(tenant_id, provider).delete)


class FirestoreTenantQuotaLimiter:
    """Transactional UTC-day quota shared by every service instance."""

    def __init__(self, project_id: str, database: str, daily_limit: int, *, client=None) -> None:
        from google.cloud import firestore

        self._client = client or firestore.Client(project=project_id, database=database)
        self._transactional = firestore.transactional
        self.daily_limit = daily_limit

    def _ref(self, tenant_id: str, provider: ConnectorProvider, day: date):
        return (
            self._client.collection("live_workspaces")
            .document(tenant_id)
            .collection("provider_quota")
            .document(f"{provider.value}-{day.isoformat()}")
        )

    async def consume(
        self,
        tenant_id: str,
        provider: ConnectorProvider,
        *,
        amount: int = 1,
        at: datetime | None = None,
    ) -> None:
        if amount < 1:
            raise ValueError("quota amount must be positive")
        now = at or datetime.now(UTC)
        ref = self._ref(tenant_id, provider, now.date())

        def reserve() -> None:
            transaction = self._client.transaction()

            @self._transactional
            def apply(transaction):
                snapshot = ref.get(transaction=transaction)
                count = (snapshot.to_dict() or {}).get("count", 0)
                if count + amount > self.daily_limit:
                    raise QuotaExceededError(
                        "Daily provider usage limit reached. Try again tomorrow."
                    )
                transaction.set(
                    ref,
                    {
                        "count": count + amount,
                        "expires_at": now + timedelta(days=2),
                    },
                )

            apply(transaction)

        await asyncio.to_thread(reserve)

    async def used(self, tenant_id: str, provider: ConnectorProvider) -> int:
        snapshot = await asyncio.to_thread(
            self._ref(tenant_id, provider, datetime.now(UTC).date()).get
        )
        return (snapshot.to_dict() or {}).get("count", 0)
