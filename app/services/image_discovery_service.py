"""Reverse image discovery across all configured providers (concurrently)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.config import Settings
from app.domain.image import ImageDiscoveryResult, ReferenceImage
from app.domain.investigation import EventType, ProviderOutcome, ProviderRun, WebEntity
from app.infrastructure.cache.store import CacheStore, cache_key
from app.providers.base import run_provider
from app.providers.reverse_image.base import ReverseImageProvider, ReverseImageQuery, ReverseImageResponse
from app.services.context import RunContext
from app.utils.canonical import canonical_image_url, canonical_url


@dataclass
class ImageDiscoveryOutcome:
    results: list[ImageDiscoveryResult] = field(default_factory=list)
    web_entities: list[WebEntity] = field(default_factory=list)
    best_guess_labels: list[str] = field(default_factory=list)


class ImageDiscoveryService:
    def __init__(self, settings: Settings, providers: list[ReverseImageProvider], cache: CacheStore | None):
        self.settings = settings
        self.providers = providers
        self.cache = cache

    def configured_providers(self) -> list[ReverseImageProvider]:
        return [p for p in self.providers if p.is_configured()]

    async def _search_one(
        self, provider: ReverseImageProvider, ref: ReferenceImage, image_bytes: bytes, ctx: RunContext
    ) -> ReverseImageResponse | None:
        key = cache_key("reverse_image", sha256=ref.sha256, **provider.cache_identity())
        if self.cache is not None:
            cached = self.cache.get("reverse_image", key)
            ctx.cache(cached is not None)
            if cached is not None:
                cached_response = ReverseImageResponse.model_validate(cached)
                for r in cached_response.results:
                    r.reference_image_id = ref.id
                ctx.record_run(
                    ProviderRun(
                        provider=provider.name,
                        stage="reverse_image",
                        outcome=(ProviderOutcome.SUCCESS if cached_response.results else ProviderOutcome.NO_RESULTS),
                        result_count=len(cached_response.results),
                        cache_hit=True,
                        detail=ref.id,
                    )
                )
                return cached_response
        response, run = await run_provider(
            provider.name,
            "reverse_image",
            lambda: provider.search(ReverseImageQuery(reference=ref, image_bytes=image_bytes)),
            timeout=self.settings.http_timeout_seconds * 3,
            detail=ref.id,
        )
        ctx.record_run(run)
        if response is not None and self.cache is not None:
            self.cache.set(
                "reverse_image", key, response.model_dump(mode="json"), self.settings.cache_ttl_reverse_image
            )
        return response

    async def discover(self, references: list[tuple[ReferenceImage, bytes]], ctx: RunContext) -> ImageDiscoveryOutcome:
        outcome = ImageDiscoveryOutcome()
        for provider in self.providers:
            if not provider.is_configured():
                ctx.record_run(
                    ProviderRun(provider=provider.name, stage="reverse_image", outcome=ProviderOutcome.NOT_CONFIGURED)
                )
        providers = self.configured_providers()
        if not providers or not references:
            if not providers:
                ctx.warn(
                    "No reverse-image provider is configured (set GOOGLE_VISION_* or TINEYE_*). "
                    "Only parameter-assisted discovery can run."
                )
            return outcome
        ctx.emit(
            EventType.REVERSE_IMAGE_SEARCH_STARTED,
            "Reverse image search started",
            providers=[p.name for p in providers],
            reference_images=len(references),
        )
        tasks = [self._search_one(p, ref, data, ctx) for p in providers for ref, data in references]
        responses = await asyncio.gather(*tasks)

        seen: set[tuple[str, str, str]] = set()
        entity_seen: set[str] = set()
        for response in responses:
            if response is None:
                continue
            new = 0
            for result in response.results:
                key = (
                    canonical_image_url(result.image_url) if result.image_url else "",
                    canonical_url(result.page_url) if result.page_url else "",
                    result.match_type.value,
                )
                if key in seen:
                    continue
                seen.add(key)
                outcome.results.append(result)
                new += 1
            for entity in response.web_entities:
                if entity.description.lower() not in entity_seen:
                    entity_seen.add(entity.description.lower())
                    outcome.web_entities.append(entity)
            for label in response.best_guess_labels:
                if label not in outcome.best_guess_labels:
                    outcome.best_guess_labels.append(label)
            if new and response.results:
                provider_name = response.results[0].provider
                counts: dict[str, int] = {}
                for r in response.results:
                    counts[r.match_type.value] = counts.get(r.match_type.value, 0) + 1
                ctx.emit(
                    EventType.REVERSE_IMAGE_RESULT_FOUND,
                    f"{provider_name}: {new} image matches",
                    provider=provider_name,
                    counts=counts,
                )
        outcome.web_entities.sort(key=lambda e: e.score or 0, reverse=True)
        ctx.stats.reverse_image_results = len(outcome.results)
        return outcome
