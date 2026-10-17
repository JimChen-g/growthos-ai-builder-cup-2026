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
        # Provider error body for server logs and debugging; never shown to the browser.
        self.detail = detail
        super().__init__(f"{provider}: {message}")


async def request_json(
    client: httpx.AsyncClient,
    provider: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> Any:
    """Make one request and convert failures into provider-safe exceptions."""

    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        logger.warning("%s request to %s failed: %s", provider, response_path(url), exc)
        raise IntegrationTransportError(f"{provider}: request failed") from exc

    if response.is_error:
        detail = response.text[:2000]
        logger.warning(
            "%s rejected %s %s with HTTP %s: %s",
            provider,
            method,
            response_path(url),
            response.status_code,
            detail,
        )
        raise IntegrationAPIError(
            provider, response.status_code, "API request was rejected", detail=detail
        )

    try:
        return response.json()
    except ValueError as exc:
        raise IntegrationAPIError(
            provider, response.status_code, "API returned invalid JSON"
        ) from exc


def response_path(url: str) -> str:
    """Log the endpoint without query strings, which may carry access tokens."""

    return str(httpx.URL(url).copy_with(query=None))
