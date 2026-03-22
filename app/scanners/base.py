"""Base scanner interface."""

from __future__ import annotations
from abc import ABC, abstractmethod
from app.models import PersonQuery, SearchResult, SocialProfile, ImageMatch


class BaseScanner(ABC):
    """Abstract base class for all OSINT scanners."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    async def scan(self, query: PersonQuery) -> list:
        """Run the scanner and return results."""
        ...

    def name_variants(self, query: PersonQuery) -> list[str]:
        """Generate name search variants."""
        variants = [query.full_name]
        if query.first_name and query.last_name:
            variants.append(f"{query.first_name} {query.last_name}")
            variants.append(f"{query.last_name}, {query.first_name}")
            variants.append(f"{query.last_name} {query.first_name}")
        # Add nickname variants
        for nick in query.nicknames:
            if query.last_name:
                variants.append(f"{nick} {query.last_name}")
            variants.append(nick)
        # Add quoted exact match
        variants.append(f'"{query.full_name}"')
        return variants

    def location_queries(self, query: PersonQuery) -> list[str]:
        """Generate location-enhanced search terms."""
        queries = []
        for loc in query.locations:
            for name in self.name_variants(query):
                queries.append(f"{name} {loc.raw}")
                if loc.city:
                    queries.append(f"{name} {loc.city}")
                if loc.state:
                    queries.append(f"{name} {loc.state}")
                # Geo-expansion (e.g., Oberhausen → NRW → Germany)
                for expanded in loc.geo_expansion:
                    queries.append(f"{name} {expanded}")
        return queries
