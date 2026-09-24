"""DuckDuckGo search via the ``ddgs`` package (fallback web search)."""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.providers.base import ProviderError, ProviderNotConfigured, ProviderRateLimited
from app.providers.web_search.base import WebSearchHit, WebSearchProvider


class DdgsProvider(WebSearchProvider):
    name = "duckduckgo"

    def __init__(self, settings: Settings):
        self.settings = settings

    def is_configured(self) -> bool:
        if not self.settings.ddgs_enabled:
            return False
        try:
            import ddgs  # noqa: F401
        except ImportError:
            return False
        return True

    async def search(self, query: str, limit: int = 10) -> list[WebSearchHit]:
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise ProviderNotConfigured("duckduckgo: 'ddgs' package not installed") from exc

        def run() -> list[dict]:
            return list(DDGS().text(query, max_results=limit) or [])

        try:
            rows = await asyncio.to_thread(run)
        except Exception as exc:
            text = str(exc).lower()
            if "ratelimit" in text or "202" in text or "429" in text:
                raise ProviderRateLimited("duckduckgo: rate limited") from exc
            if "no results" in text:
                return []
            raise ProviderError(f"duckduckgo: {type(exc).__name__}") from exc
        return [
            WebSearchHit(
                url=row["href"], title=str(row.get("title") or "")[:300], snippet=str(row.get("body") or "")[:600],
                provider=self.name, query=query,
            )
            for row in rows
            if isinstance(row, dict) and str(row.get("href", "")).startswith(("http://", "https://"))
        ]
