"""HTTP security middleware: headers, auth token, body size, rate limiting."""

from __future__ import annotations

import hmac
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import Settings
from app.infrastructure.security.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https: http:; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
TOKEN_COOKIE = "pia_token"  # noqa: S105 - cookie name, not a secret
_PUBLIC_API = {"/api/health", "/api/config", "/api/auth/token"}


def token_valid(settings: Settings, supplied: str | None) -> bool:
    if settings.app_api_token is None:
        return True
    return bool(supplied) and hmac.compare_digest(supplied or "", settings.app_api_token.get_secret_value())


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings):  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.settings = settings
        self.limiter = RateLimiter(settings.rate_limit_per_minute)
        # multipart overhead + all reference images of one request
        self.max_body = settings.max_upload_bytes * max(1, settings.max_reference_images) + 1024 * 1024

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = uuid.uuid4().hex[:12]
        path = request.url.path
        if path.startswith("/api/"):
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self.max_body:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
            if path not in _PUBLIC_API:
                supplied = request.headers.get("x-api-token") or request.cookies.get(TOKEN_COOKIE)
                if not token_valid(self.settings, supplied):
                    return JSONResponse({"detail": "Authentication required"}, status_code=401)
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                client = request.client.host if request.client else "unknown"
                if not self.limiter.allow(client):
                    return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error (request %s)", request_id)
            response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers["X-Request-ID"] = request_id
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
