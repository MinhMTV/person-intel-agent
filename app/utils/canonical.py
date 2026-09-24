"""Canonicalisation helpers shared by every layer.

One implementation for URL, domain, username, email, name and image-URL
normalisation so that deduplication behaves identically everywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src",
    "ref_url", "si", "spm", "trk", "trkinfo", "originalsubdomain", "_hsenc", "_hsmi",
}
_TRACKING_PREFIXES = ("utm_",)
_IMAGE_SIZE_PARAMS = {"s", "size", "w", "h", "width", "height", "resize", "fit", "quality", "q", "v"}

# Hosts that serve the same content on several subdomains.
_HOST_ALIASES = {
    "m.facebook.com": "facebook.com",
    "mobile.twitter.com": "x.com",
    "twitter.com": "x.com",
    "m.youtube.com": "youtube.com",
}

_MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.at", "or.at", "ac.at",
    "co.jp", "co.nz", "com.br", "com.cn", "co.in", "co.za", "com.mx", "com.tr",
}


def canonical_url(url: str | None) -> str:
    """Return a canonical form of an http(s) URL for deduplication.

    Lower-cases scheme/host, strips ``www.``/tracking params/fragments/default
    ports/trailing slashes and sorts the query string. Non-http URLs are
    returned stripped but otherwise unchanged.
    """
    if not url:
        return ""
    raw = url.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return raw
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    host = _HOST_ALIASES.get(host, host)
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    if host.endswith("linkedin.com") and path.startswith("/in/"):
        path = "/in/" + path.split("/")[2].lower()
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    query.sort()
    # Always normalise to https: the same page is served on both schemes.
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def canonical_image_url(url: str | None) -> str:
    """Canonical image URL: like :func:`canonical_url` but also drops sizing params."""
    base = canonical_url(url)
    if not base.startswith("https://"):
        return base
    parts = urlsplit(base)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _IMAGE_SIZE_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def domain_of(url: str | None) -> str:
    """Host without ``www.``/port, lower-cased (``""`` if unparsable)."""
    if not url:
        return ""
    try:
        host = (urlsplit(url.strip() if "//" in url else f"//{url.strip()}").hostname or "").lower()
    except ValueError:
        return ""
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return _HOST_ALIASES.get(host, host)


def registrable_domain(url_or_host: str | None) -> str:
    """Approximate eTLD+1 (``blog.example.co.uk`` → ``example.co.uk``).

    Used to decide whether two pages are *independent* sources.
    """
    host = domain_of(url_or_host)
    if not host or re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        return host
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_PART_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_username(username: str | None) -> str:
    if not username:
        return ""
    value = username.strip().lstrip("@").lower()
    return re.sub(r"[^a-z0-9._-]", "", value)


def normalize_email(email: str | None) -> str:
    """Lower-case, trim, drop ``+tag`` suffixes. Returns ``""`` if invalid."""
    if not email:
        return ""
    value = email.strip().lower()
    if value.startswith("mailto:"):
        value = value[7:]
    if not re.fullmatch(r"[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}", value):
        return ""
    local, domain = value.split("@", 1)
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("ß", "ss").replace("ø", "o").replace("đ", "d").replace("Đ", "D")


def normalize_name(name: str | None) -> str:
    """Accent-free, lower-case, single-spaced name (punctuation removed)."""
    if not name:
        return ""
    value = strip_accents(name).lower()
    value = re.sub(r"[^a-z0-9\s'-]", " ", value)
    value = value.replace("'", "").replace("-", " ")
    return re.sub(r"\s+", " ", value).strip()


def name_tokens(name: str | None) -> list[str]:
    return [t for t in normalize_name(name).split() if len(t) > 1]


def normalize_text(text: str | None) -> str:
    """Normalised free text for substring matching (accents/case folded)."""
    if not text:
        return ""
    value = strip_accents(text).lower()
    return re.sub(r"\s+", " ", value)


def stable_hash(payload: Any, length: int = 32) -> str:
    """Deterministic SHA-256 of a JSON-serialisable payload."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def dedupe_preserve_order(values: list[str], key=lambda v: v) -> list[str]:
    seen: set[Any] = set()
    out: list[str] = []
    for value in values:
        k = key(value)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(value)
    return out
