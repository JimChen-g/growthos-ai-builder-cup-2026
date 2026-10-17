"""Encrypted, tenant-scoped connector credentials and daily call quotas."""

import asyncio
import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from backend.models.connectors import ConnectorProvider


class VaultUnavailableError(RuntimeError):
    """The local encrypted credential vault has no configured master key."""


class CredentialVaultError(RuntimeError):
    """The encrypted vault is corrupt or cannot be decrypted with the configured key."""


class QuotaExceededError(RuntimeError):
    """A tenant exhausted its daily allowance for one provider."""


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
