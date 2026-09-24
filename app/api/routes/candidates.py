"""Candidate identities of an investigation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container, load_investigation
from app.container import Container

router = APIRouter(prefix="/api/investigations/{investigation_id}/candidates", tags=["candidates"])


@router.get("")
async def list_candidates(investigation_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id)
    candidates = inv.result.candidates if inv.result else []
    return {
        "candidates": [cand.model_dump(mode="json") for cand in candidates],
        "conclusion": inv.result.conclusion if inv.result else None,
    }


@router.get("/{candidate_id}")
async def get_candidate(
    investigation_id: str, candidate_id: str, c: Container = Depends(get_container)
) -> dict[str, Any]:
    inv = load_investigation(c, investigation_id)
    for cand in inv.result.candidates if inv.result else []:
        if cand.id == candidate_id:
            pages = [p.model_dump(mode="json") for p in inv.result.pages if p.id in cand.page_ids] if inv.result else []
            return {"candidate": cand.model_dump(mode="json"), "pages": pages}
    raise HTTPException(status_code=404, detail="Candidate not found")
