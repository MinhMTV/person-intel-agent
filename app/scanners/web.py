"""Web Search Scanner — DuckDuckGo + Bing + Yandex with fuzzy name matching."""

from __future__ import annotations
import asyncio
import itertools
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models import PersonQuery, SearchResult, Source, Confidence
from app.scanners.base import BaseScanner


class WebScanner(BaseScanner):
    """Search the web for person mentions via multiple engines."""

    name = "web"
    description = "DuckDuckGo + Bing + Yandex search with name variants and location expansion"

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

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Distribute queries across engines
            for i, search_term in enumerate(searches[:8]):
                try:
                    engine = i % 3
                    if engine == 0:
                        found = await self._search_ddg(client, search_term, headers)
                    elif engine == 1:
                        found = await self._search_bing(client, search_term, headers)
                    else:
                        found = await self._search_yandex(client, search_term, headers)
                    results.extend(found)
                    if i < 7:
                        await asyncio.sleep(1.5)
                except Exception:
                    pass

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique: list[SearchResult] = []
        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique.append(r)

        return unique

    # ------------------------------------------------------------------
    # Query building
    # ------------------------------------------------------------------

    def _build_search_queries(self, query: PersonQuery) -> list[str]:
        """Build search queries with fuzzy variants and location."""
        searches: list[str] = []

        # Basic name searches (with fuzzy variants)
        for variant in self._fuzzy_name_variants(query):
            searches.append(variant)

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
        sites = (
            [platform_sites[p] for p in selected_platforms if p in platform_sites]
            if selected_platforms
            else ["linkedin.com", "xing.com", "facebook.com", "twitter.com", "github.com", "instagram.com"]
        )
        searches.extend([f"{name} site:{site}" for site in sites])

        return searches

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
    # DuckDuckGo
    # ------------------------------------------------------------------

    async def _search_ddg(self, client: httpx.AsyncClient, query: str, headers: dict) -> list[SearchResult]:
        """Search via DuckDuckGo lite (no API key needed)."""
        results: list[SearchResult] = []
        encoded = urllib.parse.quote_plus(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"

        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.result-link"):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            if "uddg=" in href:
                parsed = urllib.parse.urlparse(href)
                params = urllib.parse.parse_qs(parsed.query)
                href = params.get("uddg", [""])[0]
                href = urllib.parse.unquote(href)

            if href and href.startswith("http") and "duckduckgo.com" not in href:
                results.append(SearchResult(
                    source=Source.WEB,
                    title=title,
                    url=href,
                    snippet="",
                    confidence=Confidence.MEDIUM,
                ))

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
