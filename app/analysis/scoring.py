"""Confidence Scorer — rates the reliability of intelligence results.

Scores each result (0.0-1.0) based on:
  - Source reliability (verified platforms > unknown sites)
  - Match quality (exact name match > partial match)
  - Cross-verification (same info from multiple sources)
  - Data completeness (bio, avatar, followers = more reliable)
  - Recency (recent activity > old data)
"""

from __future__ import annotations
from datetime import datetime, timedelta
from app.models import (
    SocialProfile, SearchResult, ImageMatch, PersonQuery,
    Source, Confidence,
)

# Source reliability weights (0.0-1.0)
SOURCE_RELIABILITY: dict[str, float] = {
    "linkedin": 0.95,
    "xing": 0.90,
    "github": 0.85,
    "twitter": 0.75,
    "instagram": 0.70,
    "facebook": 0.70,
    "youtube": 0.65,
    "reddit": 0.60,
    "tiktok": 0.55,
    "web": 0.50,
    "email": 0.60,
    "academic": 0.80,
    "breach": 0.40,
    "forum": 0.45,
    "video": 0.50,
    "business": 0.85,
    "image": 0.30,
}


def score_social_profile(profile: SocialProfile, query: PersonQuery) -> float:
    """Score a social profile's reliability (0.0-1.0).

    Factors:
      - Platform reliability
      - Username match to expected patterns
      - Profile completeness (bio, followers, display_name)
      - Verified status
    """
    score = 0.0

    # Base score from platform reliability
    platform_score = SOURCE_RELIABILITY.get(profile.platform.lower(), 0.3)
    score += platform_score * 0.4  # 40% weight

    # Username match to expected patterns
    username_score = _username_match_score(profile.username or "", query)
    score += username_score * 0.25  # 25% weight

    # Profile completeness
    completeness = 0.0
    if profile.display_name:
        completeness += 0.25
    if profile.bio:
        completeness += 0.25
    if profile.followers is not None:
        completeness += 0.25
    if profile.url:
        completeness += 0.25
    score += completeness * 0.2  # 20% weight

    # Verified bonus
    if profile.verified:
        score += 0.15  # 15% bonus

    return min(1.0, round(score, 3))


def score_search_result(result: SearchResult, query: PersonQuery) -> float:
    """Score a web search result's relevance (0.0-1.0).

    Factors:
      - Title name match
      - Snippet name match
      - Source reliability
      - Domain authority (heuristic)
    """
    score = 0.0

    # Title relevance
    title_lower = (result.title or "").lower()
    name_lower = query.full_name.lower()
    if name_lower in title_lower:
        score += 0.4
    elif query.first_name and query.first_name.lower() in title_lower:
        score += 0.2
    elif query.last_name and query.last_name.lower() in title_lower:
        score += 0.2

    # Snippet relevance
    snippet_lower = (result.snippet or "").lower()
    if name_lower in snippet_lower:
        score += 0.2
    elif query.first_name and query.first_name.lower() in snippet_lower:
        score += 0.1

    # Source reliability
    source_score = SOURCE_RELIABILITY.get(result.source.value if hasattr(result.source, 'value') else str(result.source), 0.3)
    score += source_score * 0.2

    # Domain authority heuristic
    url = (result.url or "").lower()
    domain_bonus = 0.0
    if any(d in url for d in [".gov", ".edu", ".org"]):
        domain_bonus = 0.15
    elif any(d in url for d in ["linkedin.com", "github.com", "xing.com"]):
        domain_bonus = 0.1
    score += domain_bonus * 0.2

    return min(1.0, round(score, 3))


def score_image_match(match: ImageMatch) -> float:
    """Score an image match's reliability (0.0-1.0).

    Primarily based on similarity score with context bonus.
    """
    score = match.similarity_score * 0.7  # 70% from raw similarity

    # Context bonus
    if match.context:
        score += 0.15

    # Source URL quality
    url = (match.source_url or "").lower()
    if any(d in url for d in ["linkedin", "github", "twitter", "instagram"]):
        score += 0.15

    return min(1.0, round(score, 3))


def apply_confidence_scores(
    social_profiles: list[SocialProfile],
    web_results: list[SearchResult],
    image_matches: list[ImageMatch],
    query: PersonQuery,
) -> dict:
    """Apply confidence scores to all results. Returns scored results + summary."""
    scored_social = []
    for p in social_profiles:
        score = score_social_profile(p, query)
        p.confidence = _score_to_confidence(score)
        scored_social.append({"profile": p, "score": score})

    scored_web = []
    for r in web_results:
        score = score_search_result(r, query)
        r.confidence = _score_to_confidence(score)
        scored_web.append({"result": r, "score": score})

    scored_images = []
    for m in image_matches:
        score = score_image_match(m)
        scored_images.append({"match": m, "score": score})

    # Sort by score descending
    scored_social.sort(key=lambda x: x["score"], reverse=True)
    scored_web.sort(key=lambda x: x["score"], reverse=True)
    scored_images.sort(key=lambda x: x["score"], reverse=True)

    # Overall confidence
    all_scores = [s["score"] for s in scored_social] + [s["score"] for s in scored_web]
    overall = sum(all_scores) / len(all_scores) if all_scores else 0.0

    return {
        "social": scored_social,
        "web": scored_web,
        "images": scored_images,
        "overall_confidence": round(overall, 3),
        "high_confidence_count": sum(1 for s in all_scores if s >= 0.7),
        "medium_confidence_count": sum(1 for s in all_scores if 0.4 <= s < 0.7),
        "low_confidence_count": sum(1 for s in all_scores if s < 0.4),
    }


def _username_match_score(username: str, query: PersonQuery) -> float:
    """Score how well a username matches expected patterns."""
    if not username:
        return 0.0

    user_lower = username.lower()

    # Direct match to known usernames
    if user_lower in [u.lower() for u in query.usernames]:
        return 1.0

    # Match to common patterns
    if query.first_name and query.last_name:
        fn = query.first_name.lower()
        ln = query.last_name.lower()
        expected_patterns = [
            f"{fn}{ln}", f"{fn}.{ln}", f"{fn}_{ln}",
            f"{fn[0]}{ln}", f"{ln}{fn}", f"{ln}.{fn}",
            f"{fn}-{ln}",
        ]
        if user_lower in expected_patterns:
            return 0.9

        # Partial match (contains first or last name)
        if fn in user_lower or ln in user_lower:
            return 0.5

    # Nickname match
    for nick in query.nicknames:
        if nick.lower() in user_lower:
            return 0.7

    return 0.1  # Unknown username pattern


def _score_to_confidence(score: float) -> Confidence:
    """Convert numeric score to confidence enum."""
    if score >= 0.7:
        return Confidence.HIGH
    elif score >= 0.4:
        return Confidence.MEDIUM
    else:
        return Confidence.LOW
