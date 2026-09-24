"""Investigation lifecycle, live events, evidence, notes, tags."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.api.deps import ensure_not_running, get_container, load_investigation, read_upload
from app.container import Container
from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.investigation import Investigation
from app.services.investigation_service import InvestigationError
from app.services.reference_image_service import ReferenceImageError
from app.vision.image_validation import ImageValidationError

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def investigation_view(inv: Investigation) -> dict[str, Any]:
    data = inv.model_dump(mode="json")
    data["title"] = inv.title
    return data


def summary_view(c: Container, inv: Investigation) -> dict[str, Any]:
    count, top_name, top_level = c.repo.candidate_summary(inv.id)
    return {
        "id": inv.id,
        "title": inv.title,
        "status": inv.status.value,
        "created_at": inv.created_at.isoformat(),
        "pinned": inv.pinned,
        "tags": inv.tags,
        "reference_images": len(inv.reference_images),
        "hints": inv.hints.model_dump(exclude_defaults=True),
        "candidate_count": count,
        "top_candidate": {"name": top_name, "level": top_level} if top_name else None,
    }


def _hints_from_form(**fields: Any) -> IdentityHints:
    try:
        return IdentityHints(**{k: v for k, v in fields.items() if v not in (None, "")})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json(include_url=False))) from exc


async def add_images(
    c: Container, inv_id: str, images: list[UploadFile]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    added, errors = [], []
    for upload in images:
        if not upload.filename and not upload.size:
            continue
        try:
            data = await read_upload(upload, c.settings.max_upload_bytes)
            ref = await c.investigations.add_reference_image(inv_id, upload.filename, data)
            added.append(ref.model_dump(mode="json"))
        except ImageValidationError as exc:
            errors.append({"file": upload.filename or "", "code": exc.code, "detail": exc.message})
        except ReferenceImageError as exc:
            errors.append({"file": upload.filename or "", "code": exc.code, "detail": exc.message})
        except HTTPException as exc:
            errors.append({"file": upload.filename or "", "code": "too_large", "detail": str(exc.detail)})
    return added, errors


@router.post("", status_code=201)
async def create_investigation(
    images: list[UploadFile] = File(default=[]),
    name: str | None = Form(None),
    location: str | None = Form(None),
    country: str | None = Form(None),
    age_min: int | None = Form(None),
    age_max: int | None = Form(None),
    usernames: str | None = Form(None),
    emails: str | None = Form(None),
    employer: str | None = Form(None),
    university: str | None = Form(None),
    profession: str | None = Form(None),
    known_urls: str | None = Form(None),
    start: bool = Form(False),
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    """Create an investigation (multipart). Images and all hints are optional,
    but at least one of them must be supplied. Set ``start=true`` to run immediately."""
    if len(images) > c.settings.max_reference_images:
        raise HTTPException(status_code=400, detail=f"At most {c.settings.max_reference_images} images are allowed.")
    hints = _hints_from_form(
        name=name,
        location=location,
        country=country,
        age_min=age_min,
        age_max=age_max,
        usernames=usernames,
        emails=emails,
        employer=employer,
        university=university,
        profession=profession,
        known_urls=known_urls,
    )
    inv = c.investigations.create(hints)
    added, errors = await add_images(c, inv.id, images)
    if not added and hints.is_empty():
        c.repo.delete(inv.id)
        detail = errors[0]["detail"] if errors else "Upload a photo or provide at least one identity hint."
        raise HTTPException(status_code=400, detail=detail)
    if start:
        c.runner.start(inv.id)
    inv = load_investigation(c, inv.id)
    return {"investigation": investigation_view(inv), "upload_errors": errors}


@router.get("")
async def list_investigations(
    limit: int = Query(50, ge=1, le=500),
    tag: str | None = None,
    q: str | None = None,
    c: Container = Depends(get_container),
) -> dict[str, Any]:
    items = c.investigations.list_investigations(limit=limit, tag=tag, query=q)
    return {"investigations": [summary_view(c, inv) for inv in items]}


@router.get("/tags")
async def all_tags(c: Container = Depends(get_container)) -> dict[str, Any]:
    return {"tags": c.repo.all_tags()}


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return {"investigation": investigation_view(load_investigation(c, investigation_id))}


@router.patch("/{investigation_id}")
async def update_investigation(
    investigation_id: str, body: dict = Body(...), c: Container = Depends(get_container)
) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id, with_result=False)
    ensure_not_running(c, inv)
    try:
        if "hints" in body:
            inv.hints = IdentityHints.model_validate(body["hints"] or {})
        if "options" in body:
            inv.options = InvestigationOptions.model_validate(body["options"] or {})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=json.loads(exc.json(include_url=False))) from exc
    if "pinned" in body:
        inv.pinned = bool(body["pinned"])
    c.repo.update_meta(inv)
    return {"investigation": investigation_view(load_investigation(c, investigation_id))}


@router.delete("/{investigation_id}")
async def delete_investigation(investigation_id: str, c: Container = Depends(get_container)) -> dict[str, bool]:
    inv = load_investigation(c, investigation_id, with_result=False)
    ensure_not_running(c, inv)
    for ref in inv.reference_images:
        c.reference_images.delete(inv.id, ref.id)
    c.repo.delete(inv.id)
    return {"deleted": True}


@router.post("/{investigation_id}/run", status_code=202)
async def run_investigation(investigation_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id, with_result=False)
    ensure_not_running(c, inv)
    try:
        c.runner.start(inv.id)
    except InvestigationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": inv.id, "status": "QUEUED", "events": f"/api/investigations/{inv.id}/events"}


@router.get("/{investigation_id}/events")
async def investigation_events(
    investigation_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None),
    c: Container = Depends(get_container),
) -> StreamingResponse:
    """Server-Sent Events: replays stored events, then streams live progress."""
    inv = load_investigation(c, investigation_id, with_result=False)
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    active = inv.status.is_active or c.runner.is_running(inv.id)

    async def stream():  # type: ignore[no-untyped-def]
        yield "retry: 3000\n\n"
        async for event in c.bus.subscribe(inv.id, after, is_active=active):
            if await request.is_disconnected():
                break
            if event is None:
                yield ": ping\n\n"
                continue
            payload = event.model_dump(mode="json")
            yield f"id: {event.seq}\nevent: progress\ndata: {json.dumps(payload)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


@router.get("/{investigation_id}/evidence")
async def investigation_evidence(
    investigation_id: str, type: str | None = None, c: Container = Depends(get_container)
) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id)
    rows = []
    for cand in inv.result.candidates if inv.result else []:
        for ev in cand.evidence:
            if type and ev.type.value != type.upper():
                continue
            rows.append({"candidate_id": cand.id, "candidate": cand.display_name, **ev.model_dump(mode="json")})
    return {"evidence": rows}


# --- organisation -----------------------------------------------------------------
@router.post("/{investigation_id}/notes", status_code=201)
async def add_note(
    investigation_id: str, body: dict = Body(...), c: Container = Depends(get_container)
) -> dict[str, Any]:
    load_investigation(c, investigation_id, with_result=False)
    text = str(body.get("text", "")).strip()[:5000]
    if not text:
        raise HTTPException(status_code=400, detail="Note text required")
    return {"note": c.repo.add_note(investigation_id, text).model_dump(mode="json")}


@router.delete("/{investigation_id}/notes/{note_id}")
async def delete_note(investigation_id: str, note_id: int, c: Container = Depends(get_container)) -> dict[str, bool]:
    load_investigation(c, investigation_id, with_result=False)
    if not c.repo.delete_note(investigation_id, note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True}


def _clean_tag(tag: str) -> str:
    cleaned = re.sub(r"[^\w\- ]", "", tag.strip().lower())[:40]
    if not cleaned:
        raise HTTPException(status_code=400, detail="Tag required")
    return cleaned


@router.post("/{investigation_id}/tags")
async def add_tag(
    investigation_id: str, body: dict = Body(...), c: Container = Depends(get_container)
) -> dict[str, Any]:
    load_investigation(c, investigation_id, with_result=False)
    return {"tags": c.repo.add_tag(investigation_id, _clean_tag(str(body.get("tag", ""))))}


@router.delete("/{investigation_id}/tags/{tag}")
async def remove_tag(investigation_id: str, tag: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    load_investigation(c, investigation_id, with_result=False)
    return {"tags": c.repo.remove_tag(investigation_id, _clean_tag(tag))}
