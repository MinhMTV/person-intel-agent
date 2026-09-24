"""Exports rendered from the persisted investigation result."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.api.deps import get_container, load_investigation
from app.container import Container

router = APIRouter(prefix="/api/investigations/{investigation_id}", tags=["exports"])


@router.get("/export")
async def export_investigation(
    investigation_id: str,
    format: str = Query("json", pattern="^(json|md|markdown|html|csv|pdf|zip)$"),
    c: Container = Depends(get_container),
) -> Response:
    inv = load_investigation(c, investigation_id)
    try:
        content, media_type, filename = c.reports.render(inv, format)
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="PDF export requires the 'fpdf2' package") from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
