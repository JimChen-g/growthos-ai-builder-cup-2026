"""Shared integration errors and HTTP response handling."""

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class IntegrationError(RuntimeError):
    """Base error raised by an external provider adapter."""


class IntegrationTransportError(IntegrationError):
    """The provider could not be reached."""


class IntegrationAPIError(IntegrationError):
    """The provider rejected a request or returned malformed data."""

    def __init__(
        self,
        provider: str,
        status_code: int | None,
        message: str,
        *,
        detail: str | None = None,
        operation: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        # Keep the legacy attribute/signature, but never retain provider response bodies:
        # those can echo credentials, prompts, account IDs or private business records.
        self.detail = f"HTTP {status_code}" if detail is not None and status_code else None
        self.operation = operation
        self.error_code = error_code
        super().__init__(f"{provider}: {message}")


_SAFE_PROVIDER_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _safe_provider_code(payload: Any, keys: frozenset[str]) -> str | None:
    """Extract only a provider enum, never a message, trigger, ID, or response body."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and _SAFE_PROVIDER_CODE.fullmatch(value):
                return value
            found = _safe_provider_code(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _safe_provider_code(value, keys)
            if found:
                return found
    return None


async def request_json(
    client: httpx.AsyncClient,
    provider: str,
    method: str,
    url: str,
    operation: str | None = None,
    safe_error_code_keys: frozenset[str] = frozenset(),
    **kwargs: Any,
) -> Any:
    """Make one request and convert failures into provider-safe exceptions."""

    response = None
    failure_kind = None
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        failure_kind = type(exc).__name__
    if response is None:
        # Do not log URLs or exceptions: both may contain query-string API keys or
        # tenant account IDs. Raise outside the handler to avoid retaining that cause.
        logger.warning("%s request failed (%s)", provider, failure_kind)
        raise IntegrationTransportError(f"{provider}: request failed")

    if response.is_error:
        error_code = None
        if safe_error_code_keys:
            try:
                error_code = _safe_provider_code(response.json(), safe_error_code_keys)
            except ValueError:
                pass
        logger.warning(
            "%s %s rejected %s with HTTP %s (%s)",
            provider,
            operation or "request",
            method,
            response.status_code,
            error_code or "unclassified",
        )
        raise IntegrationAPIError(
            provider,
            response.status_code,
            "API request was rejected",
            operation=operation,
            error_code=error_code,
        )

    try:
        return response.json()
    except ValueError:
        pass
    # JSONDecodeError retains its input document; do not attach it to the safe error.
    raise IntegrationAPIError(provider, response.status_code, "API returned invalid JSON")
