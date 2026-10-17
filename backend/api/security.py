"""Request-level access control for the GrowthOS API.

Two independent guards:

* **Access token.** With ``API_ACCESS_TOKEN`` set, every ``/api`` request must send
  ``Authorization: Bearer <token>``. Without a token the API answers only direct loopback
  clients, which keeps local development friction-free and makes an accidentally exposed
  server refuse remote callers instead of serving live business data.
* **Public demo mode.** ``PUBLIC_DEMO_ONLY=true`` refuses every Live route, so a hosted
  demo can never read connected accounts or spend provider quota.
"""

import hashlib
import hmac
import logging
from asyncio import to_thread
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

from backend.config import Settings

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_PUBLIC_PATHS = {"/health", "/api/v1/capabilities"}
_LIVE_ONLY_PREFIXES = ("/api/v1/connections", "/api/v1/growth-intelligence")
_SHARED_WORKSPACE_PREFIXES = (
    "/api/v1/workflows",
    "/api/v1/approvals",
    "/api/v1/audit-log",
    "/api/v1/audit-proof",
    "/api/v1/intelligence-history",
)
LOCAL_PRINCIPAL = "local"
TOKEN_PRINCIPAL = "token"
FIREBASE_PRINCIPAL = "firebase"


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _is_direct_loopback(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    proxied = any(name in request.headers for name in ("x-forwarded-for", "forwarded", "x-real-ip"))
    return client_host in _LOOPBACK_HOSTS and not proxied


def workspace_id_for_uid(uid: str) -> str:
    """Derive a non-reversible public-demo workspace identifier from a verified Firebase uid."""

    return hashlib.sha256(f"growthos-public-demo:{uid}".encode()).hexdigest()[:32]


def _verify_firebase_token(token: str, project_id: str) -> dict[str, Any]:
    claims = id_token.verify_firebase_token(
        token,
        GoogleAuthRequest(),
        audience=project_id,
    )
    if not claims or not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ValueError("Firebase token has no subject")
    return claims


def install_access_control(app: FastAPI, settings: Settings) -> None:
    """Register the access-control middleware. Call before adding CORS middleware."""

    expected = settings.api_access_token.get_secret_value() if settings.api_access_token else None

    async def access_control(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS or not path.startswith("/api"):
            return await call_next(request)

        if settings.firebase_auth_required:
            supplied = _bearer_token(request)
            if not supplied or not settings.firebase_project_id:
                return JSONResponse(
                    {"detail": "A Firebase identity token is required."},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                claims = await to_thread(
                    _verify_firebase_token,
                    supplied,
                    settings.firebase_project_id,
                )
            except Exception:  # noqa: BLE001 - never expose token verification detail
                logger.info("Rejected an invalid or expired Firebase identity token")
                return JSONResponse(
                    {"detail": "The Firebase identity token is invalid or expired."},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            uid = str(claims["sub"])
            request.state.principal = FIREBASE_PRINCIPAL
            request.state.firebase_uid = uid
            request.state.workspace_id = workspace_id_for_uid(uid)
        elif expected:
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
        if settings.public_demo_only and (
            path.startswith(_SHARED_WORKSPACE_PREFIXES)
            or (
                request.method not in {"GET", "HEAD"}
                and not path.startswith("/api/v1/competition/")
            )
        ):
            # Legacy local workflows and their ledgers share process-wide state. Public
            # visitors may mutate only the competition routes, which scope every write
            # and read to the authenticated Firebase workspace.
            return JSONResponse(
                {"detail": "Use the session-isolated AI decision demo for public actions."},
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
    if principal == FIREBASE_PRINCIPAL:
        return f"anonymous:{request.state.workspace_id[:12]}"
    if principal == TOKEN_PRINCIPAL:
        return f"{settings.api_principal_name} (API token)"
    return f"{claimed_actor} (local)"


def request_workspace_id(request: Request) -> str:
    """Return only a server-derived workspace id for competition routes."""

    return getattr(request.state, "workspace_id", "local-demo")
