"""Web Search Scanner — Google search + web scraping."""

from __future__ import annotations
from app.models import PersonQuery, SearchResult, Source, Confidence
from app.scanners.base import BaseScanner


class WebScanner(BaseScanner):
    """Search the web for person mentions."""

    name = "web"
    description = "Google search with name variants and location expansion"

    async def scan(self, query: PersonQuery) -> list[SearchResult]:
        """Run web search scan."""
        results = []

        # Generate search queries
        searches = self._build_search_queries(query)

        # Execute searches (via DuckDuckGo or direct scraping)
        import httpx
        import asyncio
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Warm up session (get cookies)
            await client.get("https://lite.duckduckgo.com/lite/", headers=headers)

            for i, search_term in enumerate(searches[:6]):  # Limit to 6 to avoid rate limits
                try:
                    found = await self._search_ddg(client, search_term, headers)
                    results.extend(found)
                    if i < 5:
                        await asyncio.sleep(1.5)  # Small delay between requests
                except Exception:
                    pass

        # Deduplicate by URL
        seen_urls = set()
        unique = []
        for r in results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique.append(r)

        return unique

    def _build_search_queries(self, query: PersonQuery) -> list[str]:
        """Build search queries with variants and location."""
        searches = []

        # Basic name searches
        for variant in self.name_variants(query):
            searches.append(variant)

        # Location-enhanced searches
        searches.extend(self.location_queries(query))

        # Site-specific searches
        name = f'"{query.full_name}"'
        searches.extend([
            f"{name} site:linkedin.com",
            f"{name} site:xing.com",
            f"{name} site:facebook.com",
            f"{name} site:twitter.com",
            f"{name} site:github.com",
        ])

        return searches

    async def _search_ddg(self, client, query: str, headers: dict = None) -> list[SearchResult]:
        """Search via DuckDuckGo lite (no API key needed)."""
        results = []
        import urllib.parse
        encoded = urllib.parse.quote_plus(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"

        resp = await client.get(url, headers=headers or {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

        if resp.status_code != 200:
            return results

        # Parse lite results
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # DuckDuckGo Lite: results are in <a> tags with class="result-link"
        for link in soup.select("a.result-link"):
            href = link.get("href", "")
            title = link.get_text(strip=True)

            # Extract real URL from DuckDuckGo redirect
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
