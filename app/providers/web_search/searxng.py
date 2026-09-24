"""SearXNG JSON API provider (self-hosted metasearch; primary web search)."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.providers.base import MalformedResponse, check_response, json_or_raise
from app.providers.web_search.base import WebSearchHit, WebSearchProvider


class SearxngProvider(WebSearchProvider):
    name = "searxng"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def is_configured(self) -> bool:
        return bool(self.settings.searxng_enabled and self.settings.searxng_url)

    async def search(self, query: str, limit: int = 10) -> list[WebSearchHit]:
        resp = await self.client.get(
            f"{self.settings.searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "safesearch": 0, "engines": self.settings.searxng_engines},
        )
        check_response(resp, self.name)
        payload = json_or_raise(resp, self.name)
        if not isinstance(payload, dict) or not isinstance(payload.get("results", []), list):
            raise MalformedResponse("searxng: unexpected response")
        hits = []
        for item in payload.get("results", [])[:limit]:
            if not isinstance(item, dict) or not str(item.get("url", "")).startswith(("http://", "https://")):
                continue
            hits.append(
                WebSearchHit(
                    url=item["url"],
                    title=str(item.get("title") or "")[:300],
                    snippet=str(item.get("content") or "")[:600],
                    provider=self.name,
                    query=query,
                    image_url=item.get("img_src") or item.get("thumbnail") or None,
                )
            )
        return hits
