"""Web Search Scanner — DuckDuckGo + Bing + Yandex with fuzzy name matching."""

from __future__ import annotations
import asyncio
import itertools
import os
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models import PersonQuery, SearchResult, Source, Confidence
from app.scanners.base import BaseScanner


class WebScanner(BaseScanner):
    """Search the web for person mentions via multiple engines."""

    name = "web"
    description = "SearXNG (primary) + DuckDuckGo/Bing/Yandex fallback with name variants and location expansion"

    async def scan(self, query: PersonQuery) -> list[SearchResult]:
        """Run web search scan across multiple engines."""
        results: list[SearchResult] = []

        # Generate enhanced search queries
        searches = self._build_search_queries(query)

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        searx_url = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
        use_searx = os.getenv("SEARXNG_ENABLED", "1") not in {"0", "false", "False"}

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Primary: SearXNG
            if use_searx:
                for i, search_term in enumerate(searches[:8]):
                    try:
                        found = await self._search_searxng(client, searx_url, search_term, headers)
                        results.extend(found)
                        if i < 7:
                            await asyncio.sleep(0.6)
                    except Exception:
                        pass

            # Fallback if SearXNG disabled/unavailable or too few hits
            if len(results) < 8:
                for i, search_term in enumerate(searches[:6]):
                    try:
                        engine = i % 3
                        if engine == 0:
                            found = await self._search_ddg(client, search_term, headers)
                        elif engine == 1:
                            found = await self._search_bing(client, search_term, headers)
                        else:
                            found = await self._search_yandex(client, search_term, headers)
                        results.extend(found)
                        if i < 5:
                            await asyncio.sleep(1.0)
                    except Exception:
                        pass

        # Deduplicate by normalized URL
        seen_urls: set[str] = set()
        unique: list[SearchResult] = []
        for r in results:
            norm = self._normalize_url(r.url)
            if norm not in seen_urls:
                seen_urls.add(norm)
                unique.append(r)

        # Filter by name matching
        from app.analysis.name_matcher import NameMatcher
        matcher = NameMatcher(query.full_name)
        filtered = matcher.filter_results(unique)

        # If too few results after filtering, keep broader set
        if len(filtered) < 5 and len(unique) >= 5:
            filtered = unique

        # Rank results for profile relevance and exact-name quality
        filtered.sort(key=lambda r: self._relevance_score(r, query), reverse=True)

        return filtered

    def _normalize_url(self, url: str) -> str:
        """Normalize URLs for reliable dedupe/comparison across engines."""
        try:
            p = urllib.parse.urlsplit((url or "").strip())
            host = (p.netloc or "").lower()
            path = (p.path or "/").rstrip("/") or "/"

            # remove locale prefixes from LinkedIn paths: /in/.. already canonical
            if host.endswith("linkedin.com") and path.startswith("/in/"):
                path = "/in/" + path.split("/in/")[-1].split("/")[0]

            return f"{host}{path}"
        except Exception:
            return (url or "").lower().strip().rstrip("/")

    def _relevance_score(self, r: SearchResult, query: PersonQuery) -> float:
        """Generic ranking for person-profile results (works for all names)."""
        url = (r.url or "").lower()
        title = (r.title or "").lower()
        snippet = (r.snippet or "").lower()
        text = f"{title} {snippet} {url}"

        parts = [p.lower() for p in query.full_name.split() if p]
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) > 1 else ""
        full = " ".join(parts)

        score = 0.0

        # Name signals
        if full and full in text:
            score += 8.0
        if first and last and first in text and last in text:
            score += 4.0

        # Profile URL patterns (generic)
        strong_patterns = ["linkedin.com/in/", "xing.com/profile/", "github.com/", "instagram.com/", "facebook.com/"]
        if any(p in url for p in strong_patterns):
            score += 3.0

        # Penalize directory/list/group pages
        bad_patterns = ["/pub/dir/", "/public/", "/groups/", "/story.php", "/watch", "/reel/"]
        if any(p in url for p in bad_patterns):
            score -= 3.0

        # Confidence bonus
        if r.confidence == Confidence.HIGH:
            score += 2.0
        elif r.confidence == Confidence.MEDIUM:
            score += 1.0

        # Optional username boost
        for u in (query.usernames or []):
            uu = u.lower().strip("@")
            if uu and uu in url:
                score += 3.0

        return score

    # ------------------------------------------------------------------
    # Query building
    # ------------------------------------------------------------------

    def _build_search_queries(self, query: PersonQuery) -> list[str]:
        """Build search queries with fuzzy variants and location."""
        searches: list[str] = []

        # Strong exact queries first
        searches.append(f'"{query.full_name}"')

        # Basic name searches (with fuzzy variants)
        for variant in self._fuzzy_name_variants(query):
            searches.append(variant)

        # Name variant searches (first+last, last+first, partial)
        name_parts = query.full_name.split()
        if len(name_parts) >= 2:
            first = name_parts[0]
            last = name_parts[-1]
            middle = name_parts[1] if len(name_parts) > 2 else None
            
            # Try different combinations
            searches.append(f'"{first} {last}"')  # "Minh Vuong"
            if middle:
                searches.append(f'"{first} {middle} {last}"')  # "Minh Tuan Vuong"
                searches.append(f'"{first} {middle}"')  # "Minh Tuan"
                searches.append(f'"{middle} {last}"')  # "Tuan Vuong"
            searches.append(f'"{last}, {first}"')  # "Vuong, Minh"
            
            # Username-style variants
            searches.append(f'{first.lower()}{last.lower()}')  # minhvuong
            if middle:
                searches.append(f'{first[0].lower()}{middle.lower()}{last.lower()}')  # mtvuong
                searches.append(f'{first.lower()}{middle[0].lower()}{last.lower()}')  # mtvuong

        # Location-enhanced searches
        searches.extend(self.location_queries(query))

        # Site-specific searches
        name = f'"{query.full_name}"'
        platform_sites = {
            "linkedin": "linkedin.com",
            "xing": "xing.com",
            "facebook": "facebook.com",
            "twitter": "twitter.com",
            "github": "github.com",
            "instagram": "instagram.com",
            "reddit": "reddit.com",
            "tiktok": "tiktok.com",
            "youtube": "youtube.com",
        }
        selected_platforms = set(p.lower() for p in query.include_platforms or [])
        excluded_platforms = set(p.lower() for p in query.exclude_platforms or [])
        
        if selected_platforms:
            sites = [platform_sites[p] for p in selected_platforms if p in platform_sites]
        else:
            sites = [platform_sites[p] for p in platform_sites if p not in excluded_platforms]
        
        searches.extend([f"{name} site:{site}" for site in sites])

        # Username-anchored profile queries (generic for all people)
        for username in query.usernames or []:
            u = username.strip().lstrip("@")
            if not u:
                continue
            for site in sites:
                searches.append(f'"{u}" site:{site}')

        # Deduplicate while preserving order
        deduped: list[str] = []
        seen: set[str] = set()
        for s in searches:
            key = s.strip().lower()
            if key and key not in seen:
                seen.add(key)
                deduped.append(s)

        return deduped

    def _fuzzy_name_variants(self, query: PersonQuery) -> list[str]:
        """Generate fuzzy name variants for broader search coverage.

        Produces: "First L.", "F. Last", "Last, First", initials-only,
        combined with parent class variants.
        """
        variants = list(self.name_variants(query))  # From BaseScanner

        fn = query.first_name
        ln = query.last_name

        if fn and ln:
            fi = fn[0].upper()
            li = ln[0].upper()
            # Abbreviated forms
            variants.extend([
                f"{fn} {li}.",            # John S.
                f"{fi}. {ln}",            # J. Smith
                f"{fi}. {li}.",           # J. S.
                f"{ln}, {fn}",            # Smith, John
                f"{ln}, {fi}.",           # Smith, J.
                f'"{fn} {li}."',         # quoted "John S."
                f'"{fi}. {ln}"',         # quoted "J. Smith"
            ])
            # Lowercase variants for social media style
            variants.append(f"{fn.lower()}.{ln.lower()}")
            variants.append(f"{fi.lower()}{ln.lower()}")

        # Add nicknames with same treatment
        for nick in query.nicknames:
            if ln:
                li = ln[0].upper()
                variants.append(f"{nick} {li}.")
                variants.append(f'"{nick} {ln}"')

        return variants

    def location_queries(self, query: PersonQuery) -> list[str]:
        """Generate location-enhanced search queries."""
        if not query.locations:
            return []
        
        queries = []
        name = f'"{query.full_name}"'
        
        for loc in query.locations:
            if loc.city:
                queries.append(f"{name} {loc.city}")
            if loc.country:
                queries.append(f"{name} {loc.country}")
            if loc.city and loc.country:
                queries.append(f"{name} {loc.city} {loc.country}")
        
        return queries

    # ------------------------------------------------------------------
    # SearXNG
    # ------------------------------------------------------------------

    async def _search_searxng(self, client: httpx.AsyncClient, base_url: str, query: str, headers: dict) -> list[SearchResult]:
        """Search via local SearXNG JSON API."""
        results: list[SearchResult] = []
        url = f"{base_url}/search"
        params = {
            "q": query,
            "format": "json",
            "language": "en-US",
            "safesearch": 0,
        }

        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            return results

        payload = resp.json()
        for r in payload.get("results", [])[:12]:
            link = r.get("url", "")
            title = r.get("title", "")
            snippet = r.get("content", "") or r.get("snippet", "") or ""
            if not link:
                continue

            confidence = Confidence.LOW
            url_lower = link.lower()
            if any(p in url_lower for p in ["/in/", "/profile/", "linkedin.com/in", "xing.com/profile"]):
                confidence = Confidence.HIGH
            elif any(p in url_lower for p in ["github.com/", "facebook.com/", "instagram.com/"]):
                confidence = Confidence.MEDIUM

            image_url = None
            if any(p in url_lower for p in ["github.com", "linkedin.com", "xing.com", "instagram.com", "facebook.com", "twitter.com", "x.com"]):
                image_url = await self._extract_profile_image(link)

            results.append(SearchResult(
                source=Source.WEB,
                title=title,
                url=link,
                snippet=snippet,
                confidence=confidence,
                image_url=image_url,
            ))

        return results

    # ------------------------------------------------------------------
    # DuckDuckGo
    # ------------------------------------------------------------------

    async def _search_ddg(self, client: httpx.AsyncClient, query: str, headers: dict) -> list[SearchResult]:
        """Search via DuckDuckGo using ddgs library."""
        results: list[SearchResult] = []
        try:
            from ddgs import DDGS
            ddgs = DDGS()
            ddg_results = ddgs.text(query, max_results=10)
            for r in ddg_results:
                url = r.get("href", "")
                title = r.get("title", "")
                snippet = r.get("body", "")
                
                # Smart confidence based on URL and content
                confidence = Confidence.LOW
                url_lower = url.lower()
                
                # High confidence: direct profile URLs
                if any(p in url_lower for p in ["/in/", "/profile/", "linkedin.com/in", "xing.com/profile"]):
                    confidence = Confidence.HIGH
                elif any(p in url_lower for p in ["github.com/", "facebook.com/", "instagram.com/"]):
                    confidence = Confidence.MEDIUM
                elif any(p in url_lower for p in [".de/", ".com/", ".org/"]):
                    confidence = Confidence.MEDIUM
                
                # Boost if name appears in title/snippet
                name_parts = query.lower().split()
                if all(part in (title + snippet).lower() for part in name_parts if len(part) > 2):
                    confidence = max(confidence, Confidence.MEDIUM)
                
                # Try to extract image from URL (for social profiles)
                image_url = None
                if any(p in url_lower for p in ["github.com", "linkedin.com", "xing.com", "instagram.com", "facebook.com", "twitter.com"]):
                    image_url = await self._extract_profile_image(url)
                
                results.append(SearchResult(
                    source=Source.WEB,
                    title=title,
                    url=url,
                    snippet=snippet,
                    confidence=confidence,
                    image_url=image_url,
                ))
        except Exception as e:
            print(f"DDGS error: {e}")
        return results

    # ------------------------------------------------------------------
    # Bing
    # ------------------------------------------------------------------

    async def _search_bing(self, client: httpx.AsyncClient, query: str, headers: dict) -> list[SearchResult]:
        """Search via Bing HTML results page."""
        results: list[SearchResult] = []
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.bing.com/search?q={encoded}"

        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "html.parser")

        # Bing results: <li class="b_algo"> contains <h2><a> and <p class="b_lineclamp2">
        for item in soup.select("li.b_algo"):
            link_el = item.select_one("h2 a")
            if not link_el:
                continue

            href = link_el.get("href", "")
            title = link_el.get_text(strip=True)

            snippet_el = item.select_one("p.b_lineclamp2, .b_caption p")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if href and href.startswith("http") and "bing.com" not in href:
                results.append(SearchResult(
                    source=Source.WEB,
                    title=title,
                    url=href,
                    snippet=snippet,
                    confidence=Confidence.MEDIUM,
                ))

        return results

    # ------------------------------------------------------------------
    # Yandex
    # ------------------------------------------------------------------

    async def _search_yandex(self, client: httpx.AsyncClient, query: str, headers: dict) -> list[SearchResult]:
        """Search via Yandex HTML results page."""
        results: list[SearchResult] = []
        encoded = urllib.parse.quote_plus(query)
        url = f"https://yandex.com/search/?text={encoded}"

        yandex_headers = {**headers, "Accept-Language": "en-US,en;q=0.9"}
        resp = await client.get(url, headers=yandex_headers)
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "html.parser")

        # Yandex results: <li class="serp-item"> with <a class="OrganicTitle-Link">
        for item in soup.select("li.serp-item"):
            link_el = item.select_one("a.OrganicTitle-Link, a.OrganicUrl-Link, h2 a")
            if not link_el:
                continue

            href = link_el.get("href", "")
            title = link_el.get_text(strip=True)

            snippet_el = item.select_one("div.Organic-ContentWrapper span, .TextContainer span")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if href and href.startswith("http"):
                results.append(SearchResult(
                    source=Source.WEB,
                    title=title,
                    url=href,
                    snippet=snippet,
                    confidence=Confidence.MEDIUM,
                ))

        return results

    async def _extract_profile_image(self, url: str) -> str | None:
        """Extract profile image from social profile URL."""
        import httpx
        from bs4 import BeautifulSoup
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as resp:
                response = await resp.get(url, headers=headers)
                if response.status_code != 200:
                    return None
                
                html = response.text
                soup = BeautifulSoup(html, "html.parser")
                
                # Check og:image meta tag (works for most platforms)
                meta = soup.select_one("meta[property='og:image']")
                if meta and meta.get("content"):
                    img_url = meta.get("content")
                    if img_url and "placeholder" not in img_url.lower():
                        return img_url
                
                # Check twitter:image meta tag
                meta = soup.select_one("meta[name='twitter:image']")
                if meta and meta.get("content"):
                    return meta.get("content")
                
                # Platform-specific selectors
                if "github.com" in url:
                    img = soup.select_one("img.avatar-user, img[src*='avatars.githubusercontent.com'], img[alt*='avatar']")
                    if img:
                        src = img.get("src", "")
                        if "avatars" in src:
                            return src
                
                elif "linkedin.com" in url:
                    img = soup.select_one("img[src*='media.licdn.com'], img[class*='profile-photo'], img[data-delayed-url*='licdn']")
                    if img:
                        return img.get("src") or img.get("data-delayed-url")
                
                elif "xing.com" in url:
                    img = soup.select_one("img[src*='ximg.xingcdn.com'], img[alt*='profile'], img[class*='profile']")
                    if img:
                        return img.get("src")
                
                elif "instagram.com" in url:
                    # Instagram blocks most scraping, try og:image first
                    meta = soup.select_one("meta[property='og:image']")
                    if meta:
                        return meta.get("content")
                
                elif "facebook.com" in url:
                    meta = soup.select_one("meta[property='og:image']")
                    if meta:
                        return meta.get("content")
                
                elif "twitter.com" in url or "x.com" in url:
                    img = soup.select_one("img[src*='pbs.twimg.com/profile_images']")
                    if img:
                        return img.get("src")
                
        except Exception as e:
            pass
        
        return None
