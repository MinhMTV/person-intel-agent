"""Candidate page analysis: fetch (SSRF-safe), parse, prioritise images."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.config import Settings
from app.domain.candidate import CandidatePage, PageProfile
from app.domain.image import CandidateImage, ImageOrigin
from app.infrastructure.cache.store import CacheStore, cache_key
from app.infrastructure.http.safe_fetcher import FetchError, SafeFetcher
from app.services.context import RunContext
from app.utils.canonical import (
    canonical_image_url,
    canonical_url,
    dedupe_preserve_order,
    name_tokens,
    normalize_email,
    registrable_domain,
    stable_hash,
)
from app.utils.platforms import SOCIAL_DOMAINS, platform_for, username_from_url

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TITLE_SPLIT = re.compile(r"\s+[|\-–—·•:]\s+")
_BAD_IMAGE_HINTS = re.compile(r"(logo|icon|sprite|emoji|banner|badge|pixel|tracking|spacer|placeholder|default[-_]?avatar)", re.I)
_GOOD_IMAGE_HINTS = re.compile(r"(avatar|profile|user|photo|portrait|headshot|author|member|team|people|staff|me\b)", re.I)
_EXCERPT_LIMIT = 6000


@dataclass
class ImageSpec:
    url: str
    origin: ImageOrigin
    priority: float


def _text(el: Any) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        el = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        content = _attr(el, "content")
        if content:
            return content.strip()
    return None


def _iter_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"})[:10]:
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                items.append(node)
                for key in ("@graph", "mainEntity", "author", "about"):
                    child = node.get(key)
                    if isinstance(child, (dict, list)):
                        stack.extend(child if isinstance(child, list) else [child])
            elif isinstance(node, list):
                stack.extend(node)
    return items


def _jsonld_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for key in ("name", "url", "contentUrl", "addressLocality"):
            if isinstance(value.get(key), str):
                return [value[key]]
        return []
    if isinstance(value, list):
        return [v for item in value for v in _jsonld_value(item)]
    return []


def clean_title(title: str | None) -> str | None:
    if not title:
        return None
    first = _TITLE_SPLIT.split(title.strip())[0].strip()
    return first[:120] or None


def parse_html(html: str, base_url: str, max_images: int) -> tuple[PageProfile, list[ImageSpec]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        if tag.name == "script" and tag.get("type") == "application/ld+json":
            continue
        tag.decompose()
    profile = PageProfile(fetched=True, final_url=base_url)
    profile.title = (_text(soup.title) if soup.title else None) or _meta(soup, "og:title")
    profile.description = _meta(soup, "og:description", "description", "twitter:description")
    profile.site_name = _meta(soup, "og:site_name")
    canonical_el = soup.find("link", rel=lambda v: bool(v) and "canonical" in v)
    canonical_href = _attr(canonical_el, "href")
    profile.canonical_url = canonical_url(urljoin(base_url, canonical_href or _meta(soup, "og:url") or base_url))

    images: list[ImageSpec] = []
    person_names: list[str] = []
    for node in _iter_jsonld(soup):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Person" not in types:
            continue
        person_names += _jsonld_value(node.get("name"))
        for img in _jsonld_value(node.get("image")):
            images.append(ImageSpec(urljoin(base_url, img), ImageOrigin.STRUCTURED_DATA, 0.95))
        address = node.get("address")
        profile.locations += _jsonld_value(address) if address else []
        profile.locations += _jsonld_value(node.get("homeLocation"))
        for key in ("worksFor", "affiliation", "alumniOf", "memberOf"):
            profile.organizations += _jsonld_value(node.get(key))
        if isinstance(node.get("jobTitle"), str):
            profile.organizations.append(node["jobTitle"])
        profile.social_links += [u for u in _jsonld_value(node.get("sameAs")) if u.startswith("http")]
        profile.emails += [e.replace("mailto:", "") for e in _jsonld_value(node.get("email"))]

    first = _meta(soup, "profile:first_name")
    last = _meta(soup, "profile:last_name")
    if first or last:
        person_names.append(" ".join(p for p in (first, last) if p))
    h1 = _text(soup.find("h1"))
    profile.profile_name = (
        person_names[0] if person_names else clean_title(_meta(soup, "og:title")) or (h1[:80] if h1 else None)
    )
    handle = _meta(soup, "profile:username")
    usernames = [handle] if handle else []
    page_handle = username_from_url(base_url)
    if page_handle:
        usernames.append(page_handle)

    for key in ("og:image", "og:image:url", "og:image:secure_url"):
        value = _meta(soup, key)
        if value:
            images.append(ImageSpec(urljoin(base_url, value), ImageOrigin.OPEN_GRAPH, 0.9))
    twitter_image = _meta(soup, "twitter:image", "twitter:image:src")
    if twitter_image:
        images.append(ImageSpec(urljoin(base_url, twitter_image), ImageOrigin.OPEN_GRAPH, 0.85))
    link_href = _attr(soup.find("link", rel=lambda v: bool(v) and "image_src" in v), "href")
    if link_href:
        images.append(ImageSpec(urljoin(base_url, link_href), ImageOrigin.OPEN_GRAPH, 0.7))

    tokens = set(name_tokens(profile.profile_name))
    for tag in soup.find_all("img")[:150]:
        if not isinstance(tag, Tag):
            continue
        src = _attr(tag, "src") or _attr(tag, "data-src") or _attr(tag, "data-lazy-src") or ""
        srcset = _attr(tag, "srcset")
        if not src and srcset:
            src = srcset.split(",")[-1].strip().split(" ")[0]
        src = src.strip()
        if not src or src.startswith("data:") or re.search(r"\.(svg|gif|ico)(\?|$)", src, re.I):
            continue
        classes: Any = tag.get("class") or []
        descriptor = " ".join(
            (src, _attr(tag, "alt") or "", " ".join(classes) if isinstance(classes, list) else str(classes),
             _attr(tag, "id") or "")
        )
        if _BAD_IMAGE_HINTS.search(descriptor):
            continue
        width = _int(_attr(tag, "width"))
        height = _int(_attr(tag, "height"))
        if (width and width < 64) or (height and height < 64):
            continue
        priority = 0.3
        if _GOOD_IMAGE_HINTS.search(descriptor):
            priority += 0.35
        alt_tokens = set(name_tokens(_attr(tag, "alt")))
        if tokens and tokens & alt_tokens:
            priority += 0.25
        if (width or 0) >= 150 or (height or 0) >= 150:
            priority += 0.1
        priority = min(priority, 0.85)  # explicit profile metadata (JSON-LD / OpenGraph) ranks first
        images.append(ImageSpec(urljoin(base_url, src), ImageOrigin.AVATAR if priority >= 0.6 else ImageOrigin.INLINE, priority))

    body_text = _text(soup.body or soup)
    for a in soup.find_all("a", href=True)[:400]:
        href = (_attr(a, "href") or "").strip()
        if href.lower().startswith("mailto:"):
            profile.emails.append(href[7:].split("?")[0])
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        profile.outbound_links.append(canonical_url(absolute))
        if registrable_domain(absolute) in SOCIAL_DOMAINS and username_from_url(absolute):
            profile.social_links.append(absolute)
    profile.emails += _EMAIL_RE.findall(body_text[:20000])

    profile.emails = dedupe_preserve_order(
        [normalize_email(e) for e in profile.emails if normalize_email(e) and not _is_boilerplate_email(e)]
    )[:10]
    profile.social_links = dedupe_preserve_order(profile.social_links, key=canonical_url)[:30]
    profile.outbound_links = dedupe_preserve_order(profile.outbound_links)[:300]
    profile.usernames = dedupe_preserve_order([u.lower() for u in usernames if u])[:10]
    profile.locations = dedupe_preserve_order([loc.strip() for loc in profile.locations if loc.strip()])[:10]
    profile.organizations = dedupe_preserve_order([o.strip() for o in profile.organizations if o.strip()])[:10]
    excerpt_parts = [profile.title or "", profile.description or "", profile.profile_name or "", body_text]
    profile.text_excerpt = " \n".join(p for p in excerpt_parts if p)[:_EXCERPT_LIMIT]

    by_url: dict[str, ImageSpec] = {}
    for spec in images:
        if not spec.url.startswith(("http://", "https://")):
            continue
        key = canonical_image_url(spec.url)
        if key not in by_url or by_url[key].priority < spec.priority:
            by_url[key] = spec
    ranked = sorted(by_url.values(), key=lambda s: s.priority, reverse=True)
    return profile, ranked[:max_images]


def _attr(el: Any, name: str) -> str | None:
    """String attribute of a bs4 element (multi-valued attributes are joined)."""
    if not isinstance(el, Tag):
        return None
    value = el.get(name)
    if value is None:
        return None
    return " ".join(value) if isinstance(value, list) else str(value)


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace("px", "").strip())
    except (TypeError, ValueError):
        return None


def _is_boilerplate_email(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    return local in {"noreply", "no-reply", "info", "support", "privacy", "abuse", "press", "hello", "contact", "webmaster"} or (
        email.lower().endswith(("example.com", "sentry.io", "wixpress.com"))
    )


class PageAnalysisService:
    def __init__(self, settings: Settings, fetcher: SafeFetcher, cache: CacheStore | None):
        self.settings = settings
        self.fetcher = fetcher
        self.cache = cache

    async def analyze(self, page: CandidatePage, ctx: RunContext) -> None:
        """Fill ``page.profile`` and append prioritised image candidates to ``page.images``."""
        if page.is_image_only:
            return
        key = cache_key("page", url=page.canonical_url, max_images=self.settings.max_images_per_page)
        cached = self.cache.get("page", key) if self.cache is not None else None
        if self.cache is not None:
            ctx.cache(cached is not None)
        if cached is not None:
            profile = PageProfile.model_validate(cached["profile"])
            specs = [ImageSpec(s["url"], ImageOrigin(s["origin"]), s["priority"]) for s in cached["images"]]
        else:
            try:
                result = await self.fetcher.fetch(page.url, kind="page")
                profile, specs = await asyncio.to_thread(
                    parse_html, result.text, result.final_url, self.settings.max_images_per_page
                )
                profile.status_code = result.status_code
            except FetchError as exc:
                profile = PageProfile(fetched=False, fetch_error=f"{exc.code}: {exc.message}"[:160],
                                      status_code=exc.status_code)
                specs = []
            if self.cache is not None and (profile.fetched or profile.fetch_error and "http_error" in profile.fetch_error):
                self.cache.set(
                    "page", key,
                    {"profile": profile.model_dump(mode="json"),
                     "images": [{"url": s.url, "origin": s.origin.value, "priority": s.priority} for s in specs]},
                    self.settings.cache_ttl_page,
                )
        if profile.fetched:
            ctx.stats.pages_fetched += 1
        # Merge: keep anything already known about the page (e.g. from profile APIs).
        existing = page.profile
        profile.usernames = dedupe_preserve_order(existing.usernames + profile.usernames)
        profile.locations = dedupe_preserve_order(existing.locations + profile.locations)
        profile.organizations = dedupe_preserve_order(existing.organizations + profile.organizations)
        profile.emails = dedupe_preserve_order(existing.emails + profile.emails)
        profile.social_links = dedupe_preserve_order(existing.social_links + profile.social_links, key=canonical_url)
        profile.outbound_links = dedupe_preserve_order(existing.outbound_links + profile.outbound_links)
        profile.profile_name = existing.profile_name or profile.profile_name
        if existing.text_excerpt:
            profile.text_excerpt = (existing.text_excerpt + " \n" + profile.text_excerpt)[:_EXCERPT_LIMIT]
        page.profile = profile
        page.title = page.title or profile.title
        page.platform = page.platform or platform_for(profile.canonical_url or page.url)
        known = {img.canonical_url for img in page.images}
        for spec in specs:
            canon = canonical_image_url(spec.url)
            if canon in known:
                continue
            known.add(canon)
            page.images.append(
                CandidateImage(
                    id=stable_hash([page.id, canon], 12), url=spec.url, canonical_url=canon, page_url=page.url,
                    origin=spec.origin, priority=spec.priority,
                )
            )
