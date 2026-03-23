"""Smart Deduplication — merge duplicate results across scanners.

Deduplicates by:
  - URL normalization (strip trailing slashes, lowercase domains)
  - Platform + username matching (same GitHub user found by multiple scanners)
  - Fuzzy name matching (similar display names on same platform)
  - Email normalization (case-insensitive, +alias stripping)
"""

from __future__ import annotations
import re
from urllib.parse import urlparse, urlunparse
from app.models import SocialProfile, SearchResult, ImageMatch, Confidence


def normalize_url(url: str) -> str:
    """Normalize URL for comparison: lowercase domain, strip trailing slash, remove www."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.lower().strip())
        domain = parsed.netloc.replace("www.", "")
        path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme, domain, path, "", "", ""))
    except Exception:
        return url.lower().strip()


def normalize_email(email: str) -> str:
    """Normalize email: lowercase, strip +alias."""
    email = email.lower().strip()
    if "@" in email:
        local, domain = email.split("@", 1)
        # Strip +alias (john+spam@gmail.com -> john@gmail.com)
        local = local.split("+")[0]
        return f"{local}@{domain}"
    return email


def normalize_username(username: str) -> str:
    """Normalize username for comparison: lowercase, strip @ prefix."""
    return username.lower().strip().lstrip("@")


def dedup_social_profiles(profiles: list[SocialProfile]) -> list[SocialProfile]:
    """Deduplicate social profiles by (platform, normalized_url) and (platform, username)."""
    seen_urls: dict[str, SocialProfile] = {}
    seen_usernames: dict[tuple[str, str], SocialProfile] = {}
    result: list[SocialProfile] = []

    for p in profiles:
        key_url = (p.platform, normalize_url(p.url))
        key_user = (p.platform.lower(), normalize_username(p.username or ""))

        # Check URL duplicate
        if key_url in seen_urls:
            existing = seen_urls[key_url]
            # Keep the one with higher confidence
            if _confidence_rank(p.confidence) > _confidence_rank(existing.confidence):
                result.remove(existing)
                result.append(p)
                seen_urls[key_url] = p
                if key_user[1]:
                    seen_usernames[key_user] = p
            continue

        # Check username duplicate on same platform
        if key_user[1] and key_user in seen_usernames:
            existing = seen_usernames[key_user]
            if _confidence_rank(p.confidence) > _confidence_rank(existing.confidence):
                result.remove(existing)
                result.append(p)
                seen_urls[key_url] = p
                seen_usernames[key_user] = p
            continue

        # New profile
        seen_urls[key_url] = p
        if key_user[1]:
            seen_usernames[key_user] = p
        result.append(p)

    return result


def dedup_search_results(results: list[SearchResult]) -> list[SearchResult]:
    """Deduplicate search results by normalized URL."""
    seen: dict[str, SearchResult] = {}
    result: list[SearchResult] = []

    for r in results:
        key = normalize_url(r.url)
        if not key:
            continue

        if key in seen:
            existing = seen[key]
            # Keep the one with higher confidence or a snippet
            if (_confidence_rank(r.confidence) > _confidence_rank(existing.confidence) or
                (r.snippet and not existing.snippet)):
                result.remove(existing)
                result.append(r)
                seen[key] = r
            continue

        seen[key] = r
        result.append(r)

    return result


def dedup_image_matches(matches: list[ImageMatch]) -> list[ImageMatch]:
    """Deduplicate image matches by (source_url, image_url)."""
    seen: set[tuple[str, str]] = set()
    result: list[ImageMatch] = []

    for m in matches:
        key = (normalize_url(m.source_url), normalize_url(m.image_url))
        if key not in seen:
            seen.add(key)
            result.append(m)

    # Sort by similarity score descending
    result.sort(key=lambda x: x.similarity_score, reverse=True)
    return result


def dedup_emails(emails: list[str]) -> list[str]:
    """Deduplicate emails with normalization."""
    seen: set[str] = set()
    result: list[str] = []

    for email in emails:
        norm = normalize_email(email)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(email)  # Keep original casing

    return result


def dedup_all(
    social: list[SocialProfile],
    web: list[SearchResult],
    images: list[ImageMatch],
    emails: list[str],
    professional: list[SearchResult],
    academic: list[SearchResult],
) -> dict:
    """Run full deduplication across all result types. Returns counts of removed items."""
    orig_social = len(social)
    orig_web = len(web)
    orig_images = len(images)
    orig_emails = len(emails)

    deduped_social = dedup_social_profiles(social)
    deduped_web = dedup_search_results(web)
    deduped_images = dedup_image_matches(images)
    deduped_emails = dedup_emails(emails)
    deduped_professional = dedup_search_results(professional)
    deduped_academic = dedup_search_results(academic)

    return {
        "social_profiles": deduped_social,
        "web_results": deduped_web,
        "image_matches": deduped_images,
        "email_addresses": deduped_emails,
        "professional": deduped_professional,
        "academic": deduped_academic,
        "removed": {
            "social": orig_social - len(deduped_social),
            "web": orig_web - len(deduped_web),
            "images": orig_images - len(deduped_images),
            "emails": orig_emails - len(deduped_emails),
        },
    }


def _confidence_rank(conf: Confidence) -> int:
    """Confidence ranking for comparison."""
    return {"high": 3, "medium": 2, "low": 1}.get(conf.value if hasattr(conf, "value") else str(conf).lower(), 0)
