"""Health, public configuration and token login."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from app import __version__
from app.api.deps import get_container
from app.api.security import TOKEN_COOKIE, token_valid
from app.container import Container
from app.services.report_service import DISCLAIMER

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/config")
async def public_config(c: Container = Depends(get_container)) -> dict[str, object]:
    s = c.settings
    return {
        "version": __version__,
        "environment": s.app_env,
        "auth_required": s.app_api_token is not None,
        "providers": c.provider_status(),
        "limits": {
            "max_upload_mb": s.max_upload_mb,
            "max_reference_images": s.max_reference_images,
            "investigation_timeout": s.investigation_timeout,
        },
        "face_bands": {
            "metric": "cosine distance (1 − cosine similarity)",
            "VERY_HIGH": f"≤ {s.face_very_high_threshold}",
            "HIGH": f"≤ {s.face_high_threshold}",
            "MEDIUM": f"≤ {s.face_match_threshold}",
            "LOW": f"≤ {s.face_low_threshold}",
            "NO_MATCH": f"> {s.face_low_threshold}",
            "note": "Bands are heuristic descriptions, not calibrated probabilities.",
        },
        "retention": {
            "reference_image_hours": s.reference_image_retention_hours,
            "reference_embedding_hours": s.reference_embedding_retention_hours,
            "face_thumbnail_days": s.face_thumbnail_retention_days,
        },
        "session_management_enabled": s.session_management_enabled,
        "disclaimer": DISCLAIMER,
    }


@router.post("/auth/token")
async def login(response: Response, body: dict = Body(...), c: Container = Depends(get_container)) -> dict[str, bool]:
    token = str(body.get("token", ""))
    if c.settings.app_api_token is None:
        return {"ok": True}
    if not token_valid(c.settings, token):
        raise HTTPException(status_code=401, detail="Invalid token")
    response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict", secure=c.settings.is_production,
                        max_age=12 * 3600)
    return {"ok": True}
