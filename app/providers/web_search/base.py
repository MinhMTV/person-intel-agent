"""Web search provider interface (used for parameter-assisted discovery)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class WebSearchHit(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    provider: str
    query: str
    image_url: str | None = None


class WebSearchProvider(ABC):
    name: str = "web_search"

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[WebSearchHit]: ...
