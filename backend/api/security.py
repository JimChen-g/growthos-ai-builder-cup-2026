"""Request-level access control for the GrowthOS API.

Two independent guards:

* **Access token.** With ``API_ACCESS_TOKEN`` set, every ``/api`` request must send
  ``Authorization: Bearer <token>``. Without a token the API answers only direct loopback
  clients, which keeps local development friction-free and makes an accidentally exposed
  server refuse remote callers instead of serving live business data.
* **Public demo mode.** ``PUBLIC_DEMO_ONLY=true`` refuses every Live route, so a hosted
  demo can never read connected accounts or spend provider quota.
"""

import hmac
import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response

from backend.config import Settings

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_PUBLIC_PATHS = {"/health"}
_LIVE_ONLY_PREFIXES = ("/api/v1/connections", "/api/v1/growth-intelligence")
LOCAL_PRINCIPAL = "local"
TOKEN_PRINCIPAL = "token"


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _is_direct_loopback(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    proxied = any(name in request.headers for name in ("x-forwarded-for", "forwarded", "x-real-ip"))
    return client_host in _LOOPBACK_HOSTS and not proxied


def install_access_control(app: FastAPI, settings: Settings) -> None:
    """Register the access-control middleware. Call before adding CORS middleware."""

    expected = settings.api_access_token.get_secret_value() if settings.api_access_token else None

    async def access_control(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS or not path.startswith("/api"):
            return await call_next(request)

        if expected:
            supplied = _bearer_token(request)
            if not supplied or not hmac.compare_digest(supplied, expected):
                return JSONResponse(
                    {"detail": "A valid API access token is required."},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.principal = TOKEN_PRINCIPAL
        elif _is_direct_loopback(request):
            request.state.principal = LOCAL_PRINCIPAL
        else:
            logger.warning("Refused remote request to %s without API_ACCESS_TOKEN", path)
            return JSONResponse(
                {
                    "detail": (
                        "Remote access is disabled. Set API_ACCESS_TOKEN on the server "
                        "and send it as a Bearer token."
                    )
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if settings.public_demo_only and (
            path.startswith(_LIVE_ONLY_PREFIXES) or request.query_params.get("mode") == "live"
        ):
            return JSONResponse(
                {"detail": "This deployment serves the Demo workspace only."},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return await call_next(request)

    app.middleware("http")(access_control)


def ensure_live_allowed(settings: Settings) -> None:
    """Guard request bodies that select Live mode (geo scans, feedback collection)."""

    if settings.public_demo_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This deployment serves the Demo workspace only.",
        )


def approver_identity(request: Request, settings: Settings, claimed_actor: str) -> str:
    """Record who approved: the authenticated principal, never only a client-typed name."""

    principal = getattr(request.state, "principal", LOCAL_PRINCIPAL)
    if principal == TOKEN_PRINCIPAL:
        return f"{settings.api_principal_name} (API token)"
    return f"{claimed_actor} (local)"
