"""Parameter-assisted candidate discovery.

Turns optional identity hints into a *small, deduplicated* set of search
queries and profile-API lookups. The output is candidate pages/profiles —
never confirmed identities.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from app.analysis.location import expand_location
from app.config import Settings
from app.domain.identity import IdentityHints
from app.domain.investigation import EventType, ProviderOutcome, ProviderRun, WebEntity
from app.infrastructure.cache.store import CacheStore, cache_key
from app.providers.base import run_provider
from app.providers.profiles.base import ProfileProvider, ProfileRecord
from app.providers.web_search.base import WebSearchHit, WebSearchProvider
from app.services.context import RunContext
from app.utils.canonical import canonical_url, dedupe_preserve_order
from app.utils.platforms import is_noise_domain

_DACH = {"DE", "AT", "CH"}


@dataclass
class PlannedQuery:
    query: str
    purpose: str


@dataclass
class DiscoveryOutcome:
    hits: list[WebSearchHit] = field(default_factory=list)
    profiles: list[ProfileRecord] = field(default_factory=list)
    entity_queries: list[str] = field(default_factory=list)


def _looks_like_person_name(text: str) -> bool:
    tokens = text.split()
    return 2 <= len(tokens) <= 4 and all(re.fullmatch(r"[^\W\d_][\w'.-]*", t) for t in tokens)


class QueryPlanner:
    def __init__(self, max_queries: int):
        self.max_queries = max_queries

    def plan(self, hints: IdentityHints, entities: list[WebEntity] | None = None) -> list[PlannedQuery]:
        queries: list[PlannedQuery] = []
        add = lambda q, p: queries.append(PlannedQuery(re.sub(r"\s+", " ", q).strip(), p))  # noqa: E731
        name = hints.name
        if name:
            quoted = f'"{name}"'
            add(quoted, "name")
            if hints.location:
                add(f"{quoted} {hints.location}", "name+location")
            elif hints.country:
                add(f"{quoted} {hints.country}", "name+country")
            if hints.employer:
                add(f'{quoted} "{hints.employer}"', "name+employer")
            if hints.university:
                add(f'{quoted} "{hints.university}"', "name+university")
            if hints.profession and not hints.employer:
                add(f"{quoted} {hints.profession}", "name+profession")
            add(f"{quoted} site:linkedin.com/in {hints.location or ''}", "linkedin")
            country_code = None
            if hints.location:
                country_code = expand_location(hints.location).country_code
            if (country_code in _DACH) or (hints.country or "").upper() in _DACH | {"GERMANY", "AUSTRIA", "SWITZERLAND"}:
                add(f"{quoted} site:xing.com/profile", "xing")
            add(f"{quoted} site:github.com", "github")
        for username in hints.usernames[:3]:
            add(f'"{username}"', "username")
        if hints.usernames:
            add(f'site:instagram.com "{hints.usernames[0]}"', "username+instagram")
        for email in hints.emails[:2]:
            add(f'"{email}"', "email")
        if not name:
            for entity in (entities or [])[:3]:
                if (entity.score or 0) >= 0.5 and _looks_like_person_name(entity.description):
                    q = f'"{entity.description}"'
                    if hints.location:
                        q += f" {hints.location}"
                    add(q, "web_entity")
                    break
        unique = dedupe_preserve_order([q.query for q in queries], key=lambda q: q.lower())
        by_query = {q.query: q for q in queries}
        return [by_query[q] for q in unique][: self.max_queries]


class CandidateDiscoveryService:
    def __init__(
        self,
        settings: Settings,
        search_providers: list[WebSearchProvider],
        profile_providers: list[ProfileProvider],
        cache: CacheStore | None,
    ):
        self.settings = settings
        self.search_providers = search_providers
        self.profile_providers = profile_providers
        self.cache = cache
        self.planner = QueryPlanner(settings.max_search_queries)

    async def _search(self, provider: WebSearchProvider, planned: PlannedQuery, ctx: RunContext) -> list[WebSearchHit] | None:
        key = cache_key("web_search", provider=provider.name, query=planned.query)
        if self.cache is not None:
            cached = self.cache.get("web_search", key)
            ctx.cache(cached is not None)
            if cached is not None:
                cached_hits = [WebSearchHit.model_validate(h) for h in cached]
                ctx.record_run(ProviderRun(provider=provider.name, stage="web_search", cache_hit=True,
                                           outcome=ProviderOutcome.SUCCESS if cached_hits else ProviderOutcome.NO_RESULTS,
                                           result_count=len(cached_hits), detail=planned.query))
                return cached_hits
        hits, run = await run_provider(
            provider.name, "web_search", lambda: provider.search(planned.query, limit=10),
            timeout=self.settings.http_timeout_seconds + 5, detail=planned.query,
        )
        ctx.record_run(run)
        if hits is not None and self.cache is not None:
            self.cache.set("web_search", key, [h.model_dump() for h in hits], self.settings.cache_ttl_web_search)
        return hits

    async def _query_with_fallback(self, planned: PlannedQuery, ctx: RunContext, sem: asyncio.Semaphore) -> list[WebSearchHit]:
        async with sem:
            for provider in self.search_providers:
                if not provider.is_configured():
                    continue
                hits = await self._search(provider, planned, ctx)
                if hits:  # fall through to the next provider on failure / no results
                    return hits
        return []

    async def _profiles(self, provider: ProfileProvider, hints: IdentityHints, ctx: RunContext) -> list[ProfileRecord]:
        key = cache_key("profile", provider=provider.name, hints=hints.normalized())
        if self.cache is not None:
            cached = self.cache.get("profile", key)
            ctx.cache(cached is not None)
            if cached is not None:
                cached_records = [ProfileRecord.model_validate(r) for r in cached]
                ctx.record_run(ProviderRun(provider=provider.name, stage="profile_lookup", cache_hit=True,
                                           outcome=ProviderOutcome.SUCCESS if cached_records else ProviderOutcome.NO_RESULTS,
                                           result_count=len(cached_records)))
                return cached_records
        records, run = await run_provider(
            provider.name, "profile_lookup", lambda: provider.lookup(hints), timeout=self.settings.http_timeout_seconds * 2
        )
        ctx.record_run(run)
        if records is not None and self.cache is not None:
            self.cache.set("profile", key, [r.model_dump() for r in records], self.settings.cache_ttl_web_search)
        return records or []

    async def discover(self, hints: IdentityHints, ctx: RunContext, entities: list[WebEntity] | None = None) -> DiscoveryOutcome:
        outcome = DiscoveryOutcome()
        plan = self.planner.plan(hints, entities) if ctx.options.use_web_search else []
        outcome.entity_queries = [p.query for p in plan if p.purpose == "web_entity"]
        profile_providers = (
            [p for p in self.profile_providers if p.is_configured() and p.applicable(hints)]
            if ctx.options.use_profile_providers else []
        )
        if not plan and not profile_providers:
            return outcome
        ctx.emit(EventType.CANDIDATE_SEARCH_STARTED, "Parameter-assisted search started",
                 queries=[p.query for p in plan], profile_providers=[p.name for p in profile_providers])
        if plan and not any(p.is_configured() for p in self.search_providers):
            ctx.warn("No web search provider is available (SearXNG/DuckDuckGo).")
        sem = asyncio.Semaphore(3)
        search_task = asyncio.gather(*(self._query_with_fallback(p, ctx, sem) for p in plan))
        profile_task = asyncio.gather(*(self._profiles(p, hints, ctx) for p in profile_providers))
        search_results, profile_results = await asyncio.gather(search_task, profile_task)

        seen: set[str] = set()
        for hits in search_results:
            for hit in hits:
                key = canonical_url(hit.url)
                if not key or key in seen or is_noise_domain(hit.url):
                    continue
                seen.add(key)
                outcome.hits.append(hit)
        for records in profile_results:
            outcome.profiles.extend(records)
        return outcome
