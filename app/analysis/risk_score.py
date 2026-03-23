"""Risk Score module — calculate how findable/exposed a person is online."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskScore:
    """Risk assessment for a person dossier."""
    score: float  # 0-100 (100 = maximum exposure)
    level: str  # low, moderate, high, critical
    factors: list[dict]
    recommendations: list[str]


def calculate_risk_score(dossier: dict) -> RiskScore:
    """Calculate risk score based on dossier data.

    Factors:
    - Social profiles (more = higher risk)
    - Web mentions (more = higher risk)
    - Image matches (face = higher risk)
    - Email addresses (public = higher risk)
    - Professional profiles (public = higher risk)
    - Locations exposed (more = higher risk)
    """
    factors = []
    score = 0.0

    # Social profiles (max 25 points)
    social_count = len(dossier.get("social_profiles", []))
    social_score = min(social_count * 5, 25)
    factors.append({
        "name": "Social Profiles",
        "count": social_count,
        "score": social_score,
        "max": 25,
        "description": f"{social_count} social profiles found",
    })
    score += social_score

    # Web results (max 20 points)
    web_count = len(dossier.get("web_results", []))
    web_score = min(web_count * 2, 20)
    factors.append({
        "name": "Web Mentions",
        "count": web_count,
        "score": web_score,
        "max": 20,
        "description": f"{web_count} web mentions found",
    })
    score += web_score

    # Image matches (max 20 points)
    img_count = len(dossier.get("image_matches", []))
    img_score = min(img_count * 10, 20)
    factors.append({
        "name": "Image Matches",
        "count": img_count,
        "score": img_score,
        "max": 20,
        "description": f"{img_count} image matches found",
    })
    score += img_score

    # Email addresses (max 15 points)
    email_count = len(dossier.get("email_addresses", []))
    email_score = min(email_count * 7, 15)
    factors.append({
        "name": "Public Emails",
        "count": email_count,
        "score": email_score,
        "max": 15,
        "description": f"{email_count} email addresses exposed",
    })
    score += email_score

    # Professional (max 10 points)
    prof_count = len(dossier.get("professional", []))
    prof_score = min(prof_count * 5, 10)
    factors.append({
        "name": "Professional Profiles",
        "count": prof_count,
        "score": prof_score,
        "max": 10,
        "description": f"{prof_count} professional profiles found",
    })
    score += prof_score

    # Confidence bonus (max 10 points)
    confidence = dossier.get("confidence_score", 0)
    conf_score = confidence * 10
    factors.append({
        "name": "Match Confidence",
        "count": f"{confidence:.0%}",
        "score": conf_score,
        "max": 10,
        "description": f"{confidence:.0%} overall match confidence",
    })
    score += conf_score

    # Determine level
    if score >= 75:
        level = "critical"
    elif score >= 50:
        level = "high"
    elif score >= 25:
        level = "moderate"
    else:
        level = "low"

    # Recommendations
    recommendations = []
    if social_count > 3:
        recommendations.append("Consider reviewing privacy settings on social profiles")
    if img_count > 0:
        recommendations.append("Face images found — consider reverse image search protection")
    if email_count > 1:
        recommendations.append("Multiple email addresses exposed — check breach databases")
    if web_count > 5:
        recommendations.append("High web presence — review and request removal from data brokers")
    if not recommendations:
        recommendations.append("Low exposure — maintain current privacy practices")

    return RiskScore(
        score=round(min(score, 100), 1),
        level=level,
        factors=factors,
        recommendations=recommendations,
    )
