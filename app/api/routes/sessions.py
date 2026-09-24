"""Browser-session management (LinkedIn/Xing/… cookies).

Disabled unless SESSION_MANAGEMENT_ENABLED=true. Even then, requests must come
from the local machine unless an APP_API_TOKEN protects the API. Cookie values
are never returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.deps import get_container, is_loopback
from app.container import Container
from app.infrastructure.security.session_store import PLATFORMS
from app.services.session_service import interactive_login


def require_session_management(request: Request, c: Container = Depends(get_container)) -> Container:
    if not c.settings.session_management_enabled:
        raise HTTPException(status_code=404, detail="Session management is disabled")
    if c.settings.app_api_token is None and not is_loopback(request):
        raise HTTPException(status_code=403, detail="Session management is only available from localhost")
    return c


router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _platform(platform: str) -> str:
    if platform not in PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown platform")
    return platform


@router.get("")
async def list_sessions(c: Container = Depends(require_session_management)) -> dict[str, Any]:
    return {"sessions": c.sessions.all_status()}


@router.post("/{platform}/cookies")
async def import_cookies(
    platform: str, body: dict = Body(...), c: Container = Depends(require_session_management)
) -> dict[str, Any]:
    cookies = body.get("cookies")
    if not isinstance(cookies, list) or len(cookies) > 500:
        raise HTTPException(status_code=400, detail="Expected a JSON array of cookies")
    try:
        count = c.sessions.save(_platform(platform), cookies)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "cookie_count": count}


@router.post("/{platform}/login")
async def browser_login(
    platform: str, request: Request, c: Container = Depends(require_session_management)
) -> dict[str, Any]:
    if not is_loopback(request):
        raise HTTPException(status_code=403, detail="Interactive login opens a browser on the server; localhost only")
    result = await interactive_login(c.sessions, _platform(platform))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Login failed"))
    return result


@router.delete("/{platform}")
async def delete_session(platform: str, c: Container = Depends(require_session_management)) -> dict[str, bool]:
    if not c.sessions.delete(_platform(platform)):
        raise HTTPException(status_code=404, detail="No stored session")
    return {"deleted": True}
