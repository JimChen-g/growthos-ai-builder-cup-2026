"""Shared integration errors and HTTP response handling."""

import logging
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
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        # Keep the legacy attribute/signature, but never retain provider response bodies:
        # those can echo credentials, prompts, account IDs or private business records.
        self.detail = f"HTTP {status_code}" if detail is not None and status_code else None
        super().__init__(f"{provider}: {message}")


async def request_json(
    client: httpx.AsyncClient,
    provider: str,
    method: str,
    url: str,
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
        logger.warning(
            "%s rejected %s with HTTP %s",
            provider,
            method,
            response.status_code,
        )
        raise IntegrationAPIError(
            provider, response.status_code, "API request was rejected"
        )

    try:
        return response.json()
    except ValueError:
        pass
    # JSONDecodeError retains its input document; do not attach it to the safe error.
    raise IntegrationAPIError(provider, response.status_code, "API returned invalid JSON")
