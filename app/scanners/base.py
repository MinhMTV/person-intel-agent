"""Base scanner interface."""

from __future__ import annotations
from abc import ABC, abstractmethod
from app.models import PersonQuery, SearchResult, SocialProfile, ImageMatch
from app.analysis.location import expand_location, build_location_queries


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
        """Generate location-enhanced search terms with geo-expansion."""
        queries = []

        # Use location intelligence for expansion
        for loc in query.locations:
            for name in self.name_variants(query):
                # Original location
                queries.append(f"{name} {loc.raw}")

                # Expanded terms
                expansion = expand_location(loc.raw)
                for term in expansion.search_terms[1:]:  # Skip the original city
                    queries.append(f"{name} {term}")

                # Existing geo_expansion field
                for expanded in loc.geo_expansion:
                    queries.append(f"{name} {expanded}")

        # Also handle countries directly
        for country in query.countries:
            for name in self.name_variants(query):
                queries.append(f"{name} {country}")

        return queries
