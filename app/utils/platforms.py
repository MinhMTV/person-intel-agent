"""Knowledge about well-known platforms: profile URL patterns and source quality."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.utils.canonical import domain_of, normalize_username, registrable_domain

# platform → (registrable domain(s), profile-path regex capturing the username)
_PROFILE_PATTERNS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "linkedin": (("linkedin.com",), r"^/in/([^/]+)"),
    "xing": (("xing.com",), r"^/profile/([^/]+)"),
    "github": (("github.com",), r"^/([A-Za-z0-9-]{1,39})/?$"),
    "gitlab": (("gitlab.com",), r"^/([A-Za-z0-9._-]+)/?$"),
    "instagram": (("instagram.com",), r"^/([A-Za-z0-9._]{1,30})/?$"),
    "x": (("x.com", "twitter.com"), r"^/([A-Za-z0-9_]{1,15})/?$"),
    "facebook": (("facebook.com",), r"^/([A-Za-z0-9.]{3,})/?$"),
    "tiktok": (("tiktok.com",), r"^/@([A-Za-z0-9._]+)"),
    "youtube": (("youtube.com",), r"^/@([A-Za-z0-9._-]+)"),
    "reddit": (("reddit.com",), r"^/(?:user|u)/([A-Za-z0-9_-]+)"),
    "medium": (("medium.com",), r"^/@([A-Za-z0-9._-]+)"),
    "stackoverflow": (("stackoverflow.com",), r"^/users/\d+/([A-Za-z0-9-]+)"),
    "researchgate": (("researchgate.net",), r"^/profile/([^/]+)"),
    "orcid": (("orcid.org",), r"^/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])"),
    "gravatar": (("gravatar.com",), r"^/([A-Za-z0-9._-]+)/?$"),
    "wikipedia": (("wikipedia.org",), None),
    "wikidata": (("wikidata.org",), None),
    "pinterest": (("pinterest.com", "pinterest.de"), r"^/([A-Za-z0-9_]+)/?$"),
}

_RESERVED_PATHS = {
    "about", "login", "signup", "search", "explore", "home", "settings", "help", "privacy", "terms", "share",
    "sharer", "intent", "hashtag", "p", "reel", "watch", "groups", "pages", "events", "marketplace", "topics",
    "features", "pricing", "orgs", "sponsors", "notifications", "stories", "i", "dir", "pub", "company", "jobs",
}

HIGH_QUALITY_PLATFORMS = {"linkedin", "xing", "github", "gitlab", "orcid", "researchgate", "wikipedia", "wikidata", "stackoverflow"}
LOW_QUALITY_DOMAINS = {
    "pinterest.com", "pinterest.de", "whitepages.com", "spokeo.com", "radaris.com", "peekyou.com",
    "idcrawl.com", "clustrmaps.com", "mylife.com", "fastpeoplesearch.com",
}
NOISE_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "yandex.com", "yandex.ru", "archive.org", "whois.com",
    "taxirideestimate.com", "goodreads.com", "gstatic.com", "googleusercontent.com",
}
SOCIAL_DOMAINS = {d for domains, _ in _PROFILE_PATTERNS.values() for d in domains}


def platform_for(url: str | None) -> str | None:
    reg = registrable_domain(url)
    for platform, (domains, _) in _PROFILE_PATTERNS.items():
        if reg in domains:
            return platform
    host = domain_of(url)
    if host.endswith(".wikipedia.org"):
        return "wikipedia"
    return None


def username_from_url(url: str | None) -> str | None:
    """Extract the account handle from a profile URL (None for non-profile URLs)."""
    platform = platform_for(url)
    if not platform or not url:
        return None
    pattern = _PROFILE_PATTERNS[platform][1]
    if not pattern:
        return None
    path = urlsplit(url).path or "/"
    match = re.match(pattern, path)
    if not match:
        return None
    handle = normalize_username(match.group(1))
    if not handle or handle in _RESERVED_PATHS:
        return None
    return handle


def is_profile_url(url: str | None) -> bool:
    return username_from_url(url) is not None or platform_for(url) in ("wikipedia", "wikidata")


def source_quality(url: str | None) -> float:
    """Heuristic reliability of a source type (0..1). Not a probability."""
    reg = registrable_domain(url)
    host = domain_of(url)
    if reg in LOW_QUALITY_DOMAINS:
        return 0.3
    platform = platform_for(url)
    if platform in HIGH_QUALITY_PLATFORMS and is_profile_url(url):
        return 0.85
    if re.search(r"\.(edu|gov|ac\.[a-z]{2}|uni-[a-z-]+\.[a-z]{2})$", host) or host.startswith(("uni-", "tu-")):
        return 0.8
    if platform and is_profile_url(url):
        return 0.65
    return 0.5


def is_noise_domain(url: str | None) -> bool:
    return registrable_domain(url) in NOISE_DOMAINS
