"""Reference image management (upload, preview, target-face selection)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.api.deps import ensure_not_running, get_container, load_investigation
from app.api.routes.investigations import add_images
from app.container import Container
from app.services.reference_image_service import ReferenceImageError

router = APIRouter(prefix="/api/investigations/{investigation_id}/reference-images", tags=["images"])


@router.post("", status_code=201)
async def upload_reference_images(
    investigation_id: str, images: list[UploadFile] = File(...), c: Container = Depends(get_container)
) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id, with_result=False)
    ensure_not_running(c, inv)
    added, errors = await add_images(c, inv.id, images)
    if not added and errors:
        raise HTTPException(status_code=400, detail=errors[0]["detail"])
    return {"reference_images": added, "upload_errors": errors}


@router.get("/{image_id}/image")
async def reference_image_file(investigation_id: str, image_id: str, c: Container = Depends(get_container)) -> Response:
    inv = load_investigation(c, investigation_id, with_result=False)
    ref = next((r for r in inv.reference_images if r.id == image_id), None)
    data = c.reference_images.read_bytes(ref) if ref else None
    if data is None:
        raise HTTPException(
            status_code=404, detail="Image not available (it may have expired under the retention policy)"
        )
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})


@router.put("/{image_id}/face")
async def select_target_face(
    investigation_id: str, image_id: str, body: dict = Body(...), c: Container = Depends(get_container)
) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id, with_result=False)
    ensure_not_running(c, inv)
    try:
        ref = c.reference_images.select_face(inv.id, image_id, str(body.get("face_id", "")))
    except ReferenceImageError as exc:
        raise HTTPException(status_code=404 if exc.code == "not_found" else 400, detail=exc.message) from exc
    return {"reference_image": ref.model_dump(mode="json")}


@router.delete("/{image_id}")
async def delete_reference_image(
    investigation_id: str, image_id: str, c: Container = Depends(get_container)
) -> dict[str, bool]:
    inv = load_investigation(c, investigation_id, with_result=False)
    ensure_not_running(c, inv)
    if not c.reference_images.delete(inv.id, image_id):
        raise HTTPException(status_code=404, detail="Reference image not found")
    return {"deleted": True}
