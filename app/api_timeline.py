"""Dossier Timeline — chronological view of all findings.

Creates a unified timeline from:
  - Social profile discovery order
  - Web result discovery order
  - Image match discovery order
  - Scanner execution order (which scanner found what)

Provides API endpoints for timeline view and filtering.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException

from app.models import Confidence

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


def _get_dossiers():
    from app.web import _dossiers
    return _dossiers


def build_timeline(dossier) -> list[dict]:
    """Build a chronological timeline from dossier results."""
    timeline = []
    order = 0

    # Social profiles
    for p in dossier.social_profiles:
        order += 1
        timeline.append({
            "order": order,
            "type": "social",
            "platform": p.platform,
            "title": p.display_name or p.username or p.platform,
            "url": p.url,
            "confidence": p.confidence.value if hasattr(p.confidence, 'value') else str(p.confidence),
            "verified": p.verified,
            "bio": p.bio,
            "followers": p.followers,
            "details": {
                "username": p.username,
                "platform": p.platform,
            },
        })

    # Web results
    for r in dossier.web_results:
        order += 1
        timeline.append({
            "order": order,
            "type": "web",
            "platform": r.source.value if hasattr(r.source, 'value') else str(r.source),
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "confidence": r.confidence.value if hasattr(r.confidence, 'value') else str(r.confidence),
            "verified": False,
            "details": {
                "source": r.source.value if hasattr(r.source, 'value') else str(r.source),
            },
        })

    # Image matches
    for m in dossier.image_matches:
        order += 1
        timeline.append({
            "order": order,
            "type": "image",
            "platform": "image_search",
            "title": f"Image Match ({(m.similarity_score * 100):.0f}% similarity)",
            "url": m.source_url,
            "confidence": "high" if m.similarity_score > 0.8 else "medium" if m.similarity_score > 0.6 else "low",
            "verified": False,
            "details": {
                "image_url": m.image_url,
                "similarity": m.similarity_score,
                "context": m.context,
            },
        })

    # Professional results
    for r in dossier.professional:
        order += 1
        timeline.append({
            "order": order,
            "type": "professional",
            "platform": r.source.value if hasattr(r.source, 'value') else str(r.source),
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "confidence": r.confidence.value if hasattr(r.confidence, 'value') else str(r.confidence),
            "verified": False,
            "details": {},
        })

    # Academic results
    for r in dossier.academic:
        order += 1
        timeline.append({
            "order": order,
            "type": "academic",
            "platform": "academic",
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "confidence": r.confidence.value if hasattr(r.confidence, 'value') else str(r.confidence),
            "verified": False,
            "details": {},
        })

    # Emails
    for email in dossier.email_addresses:
        order += 1
        timeline.append({
            "order": order,
            "type": "email",
            "platform": "email",
            "title": email,
            "url": f"mailto:{email}",
            "confidence": "medium",
            "verified": False,
            "details": {"address": email},
        })

    return timeline


@router.get("/{dossier_id}")
async def get_timeline(
    dossier_id: str,
    type_filter: Optional[str] = None,
    confidence_filter: Optional[str] = None,
    platform_filter: Optional[str] = None,
    limit: int = 100,
):
    """Get timeline for a dossier with optional filters."""
    dossiers = _get_dossiers()
    dossier = dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")

    timeline = build_timeline(dossier)

    # Apply filters
    if type_filter:
        types = [t.strip() for t in type_filter.split(",")]
        timeline = [t for t in timeline if t["type"] in types]

    if confidence_filter:
        timeline = [t for t in timeline if t["confidence"] == confidence_filter]

    if platform_filter:
        platforms = [p.strip().lower() for p in platform_filter.split(",")]
        timeline = [t for t in timeline if t["platform"].lower() in platforms]

    # Limit
    timeline = timeline[:limit]

    # Summary stats
    type_counts = {}
    for t in timeline:
        type_counts[t["type"]] = type_counts.get(t["type"], 0) + 1

    return {
        "dossier_id": dossier_id,
        "name": dossier.query.full_name,
        "total_events": len(timeline),
        "type_counts": type_counts,
        "timeline": timeline,
    }


@router.get("/{dossier_id}/summary")
async def timeline_summary(dossier_id: str):
    """Get a compact timeline summary."""
    dossiers = _get_dossiers()
    dossier = dossiers.get(dossier_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dossier not found")

    timeline = build_timeline(dossier)

    # Top platforms
    platforms = {}
    for t in timeline:
        p = t["platform"]
        if p not in platforms:
            platforms[p] = {"count": 0, "verified": 0, "high_confidence": 0}
        platforms[p]["count"] += 1
        if t.get("verified"):
            platforms[p]["verified"] += 1
        if t.get("confidence") == "high":
            platforms[p]["high_confidence"] += 1

    # High confidence items
    high_conf = [t for t in timeline if t["confidence"] == "high"]

    return {
        "dossier_id": dossier_id,
        "name": dossier.query.full_name,
        "total_events": len(timeline),
        "platforms": platforms,
        "high_confidence_count": len(high_conf),
        "top_findings": high_conf[:5],
        "scanners_used": dossier.scanners_used,
    }
