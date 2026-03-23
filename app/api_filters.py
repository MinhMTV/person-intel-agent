"""Additional API endpoints for filtering, analytics, and enhanced features."""

from __future__ import annotations

import json
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.models import Confidence, Source

router = APIRouter(prefix="/api/v2", tags=["filters"])


def get_dossiers():
    """Import dossiers from web module."""
    from app.web import _dossiers, _search_history
    return _dossiers, _search_history


@router.get("/dossier/{dossier_id}/filter")
async def filter_dossier(
    dossier_id: str,
    platform: Optional[str] = Query(None, description="Filter by platform (github, twitter, linkedin, ...)"),
    confidence: Optional[str] = Query(None, description="Filter by confidence (high, medium, low)"),
    source: Optional[str] = Query(None, description="Filter by source (web, social, email, ...)"),
    min_similarity: Optional[float] = Query(None, ge=0.0, le=1.0, description="Min image similarity score"),
    has_bio: Optional[bool] = Query(None, description="Only profiles with bio"),
    verified_only: Optional[bool] = Query(None, description="Only verified profiles"),
    sort_by: Optional[str] = Query("confidence", description="Sort by: confidence, platform, similarity"),
):
    """Filter dossier results with multiple criteria."""
    dossiers, _ = get_dossiers()
    dossier = dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")

    # Filter social profiles
    social = list(dossier.social_profiles)
    if platform:
        social = [p for p in social if p.platform.lower() == platform.lower()]
    if confidence:
        social = [p for p in social if p.confidence.value == confidence]
    if has_bio:
        social = [p for p in social if p.bio]
    if verified_only:
        social = [p for p in social if p.verified]

    # Filter web results
    web = list(dossier.web_results)
    if source:
        web = [r for r in web if r.source.value == source.lower()]
    if confidence:
        web = [r for r in web if r.confidence.value == confidence]

    # Filter image matches
    images = list(dossier.image_matches)
    if min_similarity:
        images = [m for m in images if m.similarity_score >= min_similarity]

    # Sort
    if sort_by == "confidence":
        conf_order = {"high": 0, "medium": 1, "low": 2}
        social.sort(key=lambda p: conf_order.get(p.confidence.value, 3))
        web.sort(key=lambda r: conf_order.get(r.confidence.value, 3))
    elif sort_by == "platform":
        social.sort(key=lambda p: p.platform)
    elif sort_by == "similarity":
        images.sort(key=lambda m: m.similarity_score, reverse=True)

    return {
        "id": dossier_id,
        "query": {"name": dossier.query.full_name},
        "filters_applied": {
            "platform": platform,
            "confidence": confidence,
            "source": source,
            "min_similarity": min_similarity,
            "has_bio": has_bio,
            "verified_only": verified_only,
        },
        "results": {
            "social_profiles": json.loads(json.dumps([p.model_dump() for p in social], default=str)),
            "web_results": json.loads(json.dumps([r.model_dump() for r in web], default=str)),
            "image_matches": json.loads(json.dumps([m.model_dump() for m in images], default=str)),
            "email_addresses": dossier.email_addresses,
            "professional": json.loads(json.dumps([r.model_dump() for r in dossier.professional], default=str)),
        },
        "counts": {
            "social_profiles": len(social),
            "web_results": len(web),
            "image_matches": len(images),
            "email_addresses": len(dossier.email_addresses),
        },
    }


@router.get("/dossier/{dossier_id}/analytics")
async def dossier_analytics(dossier_id: str):
    """Get analytics/statistics for a dossier."""
    dossiers, _ = get_dossiers()
    dossier = dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")

    # Platform breakdown
    platforms = {}
    for p in dossier.social_profiles:
        platforms[p.platform] = platforms.get(p.platform, 0) + 1

    # Source breakdown
    sources = {}
    for r in dossier.web_results:
        src = r.source.value
        sources[src] = sources.get(src, 0) + 1

    # Confidence distribution
    confidence_dist = {"high": 0, "medium": 0, "low": 0}
    for p in dossier.social_profiles:
        confidence_dist[p.confidence.value] += 1
    for r in dossier.web_results:
        confidence_dist[r.confidence.value] += 1

    # Best image match
    best_image = None
    if dossier.image_matches:
        best = max(dossier.image_matches, key=lambda m: m.similarity_score)
        best_image = {
            "similarity": best.similarity_score,
            "url": best.image_url,
            "source": best.source_url,
        }

    return {
        "id": dossier_id,
        "name": dossier.query.full_name,
        "confidence_score": dossier.confidence_score,
        "platforms": platforms,
        "sources": sources,
        "confidence_distribution": confidence_dist,
        "totals": {
            "social_profiles": len(dossier.social_profiles),
            "web_results": len(dossier.web_results),
            "image_matches": len(dossier.image_matches),
            "email_addresses": len(dossier.email_addresses),
            "professional": len(dossier.professional),
            "academic": len(dossier.academic),
            "total_results": (
                len(dossier.social_profiles) + len(dossier.web_results)
                + len(dossier.image_matches) + len(dossier.email_addresses)
            ),
        },
        "best_image_match": best_image,
        "scanners_used": dossier.scanners_used,
    }


@router.get("/dossier/{dossier_id}/platforms")
async def list_platforms(dossier_id: str):
    """List all platforms found for a dossier."""
    dossiers, _ = get_dossiers()
    dossier = dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")

    platforms = {}
    for p in dossier.social_profiles:
        if p.platform not in platforms:
            platforms[p.platform] = []
        platforms[p.platform].append({
            "username": p.username,
            "url": p.url,
            "confidence": p.confidence.value,
            "verified": p.verified,
        })

    return {"platforms": platforms, "total_platforms": len(platforms)}


@router.get("/search/compare")
async def compare_dossiers(id1: str = Query(...), id2: str = Query(...)):
    """Compare two dossiers side by side."""
    dossiers, _ = get_dossiers()
    d1 = dossiers.get(id1)
    d2 = dossiers.get(id2)

    if not d1:
        raise HTTPException(status_code=404, detail=f"Dossier {id1} not found")
    if not d2:
        raise HTTPException(status_code=404, detail=f"Dossier {id2} not found")

    # Find shared platforms
    platforms1 = {p.platform for p in d1.social_profiles}
    platforms2 = {p.platform for p in d2.social_profiles}
    shared_platforms = platforms1 & platforms2
    unique_to_1 = platforms1 - platforms2
    unique_to_2 = platforms2 - platforms1

    # Find shared emails
    emails1 = set(d1.email_addresses)
    emails2 = set(d2.email_addresses)
    shared_emails = emails1 & emails2

    return {
        "dossier_1": {"id": id1, "name": d1.query.full_name, "results": len(d1.social_profiles) + len(d1.web_results)},
        "dossier_2": {"id": id2, "name": d2.query.full_name, "results": len(d2.social_profiles) + len(d2.web_results)},
        "shared_platforms": list(shared_platforms),
        "unique_to_1": list(unique_to_1),
        "unique_to_2": list(unique_to_2),
        "shared_emails": list(shared_emails),
        "connection_score": len(shared_platforms) + len(shared_emails) * 2,
    }


@router.get("/platforms/summary")
async def all_platforms_summary():
    """Summary of all platforms found across all dossiers."""
    dossiers, _ = get_dossiers()

    platform_stats = {}
    for dossier in dossiers.values():
        for p in dossier.social_profiles:
            if p.platform not in platform_stats:
                platform_stats[p.platform] = {"count": 0, "verified": 0, "high_confidence": 0}
            platform_stats[p.platform]["count"] += 1
            if p.verified:
                platform_stats[p.platform]["verified"] += 1
            if p.confidence == Confidence.HIGH:
                platform_stats[p.platform]["high_confidence"] += 1

    return {
        "platforms": platform_stats,
        "total_dossiers": len(dossiers),
    }
