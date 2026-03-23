"""Search Suggestions API — auto-complete based on search history and patterns."""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/suggest", tags=["suggest"])


def _get_search_history():
    from app.web import _search_history
    return _search_history


@router.get("/names")
async def suggest_names(q: str = Query(..., min_length=1, description="Partial name query")):
    """Suggest names from search history based on partial input."""
    history = _get_search_history()
    query_lower = q.lower().strip()
    
    suggestions = []
    seen = set()
    
    for entry in history:
        name = entry.get("name", "")
        if name and query_lower in name.lower() and name not in seen:
            seen.add(name)
            suggestions.append({
                "name": name,
                "last_searched": entry.get("timestamp"),
                "results_count": sum(entry.get("results", {}).values()) if entry.get("results") else 0,
            })
    
    # Sort by relevance (starts with query first, then contains)
    suggestions.sort(key=lambda s: (
        0 if s["name"].lower().startswith(query_lower) else 1,
        s["name"].lower()
    ))
    
    return {"query": q, "suggestions": suggestions[:10]}


@router.get("/locations")
async def suggest_locations(q: str = Query(..., min_length=1, description="Partial location query")):
    """Suggest locations from our geo database."""
    from app.analysis.location import ALL_LOCATIONS
    
    query_lower = q.lower().strip()
    matches = []
    
    for city, info in ALL_LOCATIONS.items():
        if query_lower in city.lower():
            matches.append({
                "city": city.title(),
                "state": info.get("state", ""),
                "country": info.get("country", ""),
                "country_code": info.get("country_code", ""),
            })
        elif info.get("state") and query_lower in info["state"].lower():
            matches.append({
                "city": city.title(),
                "state": info.get("state", ""),
                "country": info.get("country", ""),
                "country_code": info.get("country_code", ""),
            })
        elif info.get("state_abbr") and query_lower in info["state_abbr"].lower():
            matches.append({
                "city": city.title(),
                "state": info.get("state", ""),
                "country": info.get("country", ""),
                "country_code": info.get("country_code", ""),
            })
    
    # Sort by relevance (city match first, then state match)
    matches.sort(key=lambda m: (
        0 if query_lower in m["city"].lower() else 1,
        m["city"]
    ))
    
    return {"query": q, "suggestions": matches[:10]}


@router.get("/platforms")
async def suggest_platforms(q: str = Query("", description="Partial platform query")):
    """Suggest social media platforms."""
    platforms = [
        {"id": "github", "name": "GitHub", "icon": "💻"},
        {"id": "twitter", "name": "Twitter/X", "icon": "🐦"},
        {"id": "linkedin", "name": "LinkedIn", "icon": "💼"},
        {"id": "instagram", "name": "Instagram", "icon": "📸"},
        {"id": "facebook", "name": "Facebook", "icon": "📘"},
        {"id": "reddit", "name": "Reddit", "icon": "🔶"},
        {"id": "youtube", "name": "YouTube", "icon": "🎬"},
        {"id": "tiktok", "name": "TikTok", "icon": "🎵"},
        {"id": "xing", "name": "Xing", "icon": "🔷"},
        {"id": "stackoverflow", "name": "StackOverflow", "icon": "📚"},
        {"id": "medium", "name": "Medium", "icon": "✍️"},
        {"id": "twitch", "name": "Twitch", "icon": "🎮"},
    ]
    
    if q:
        q_lower = q.lower()
        platforms = [p for p in platforms if q_lower in p["id"] or q_lower in p["name"].lower()]
    
    return {"query": q, "suggestions": platforms}
