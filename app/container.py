"""Composition root: builds every service once. API, CLI and tests use it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, ensure_data_dirs, get_settings
from app.infrastructure.cache.store import CacheStore
from app.infrastructure.http.safe_fetcher import SafeFetcher, create_api_client
from app.infrastructure.persistence.repository import InvestigationRepository
from app.infrastructure.persistence.sqlite import create_repository
from app.infrastructure.security.session_store import SessionStore
from app.providers.profiles.base import ProfileProvider
from app.providers.profiles.github import GitHubProfileProvider
from app.providers.profiles.gravatar import GravatarProfileProvider
from app.providers.profiles.wikidata import WikidataProfileProvider
from app.providers.reverse_image.base import ReverseImageProvider
from app.providers.reverse_image.google_vision import GoogleCloudVisionWebDetectionProvider
from app.providers.reverse_image.tineye import TinEyeProvider
from app.providers.web_search.base import WebSearchProvider
from app.providers.web_search.ddgs_provider import DdgsProvider
from app.providers.web_search.searxng import SearxngProvider
from app.services.candidate_clustering_service import CandidateClusteringService
from app.services.candidate_discovery_service import CandidateDiscoveryService
from app.services.candidate_image_service import CandidateImageService
from app.services.candidate_ranking_service import CandidateRankingService
from app.services.evidence_fusion_service import EvidenceFusionService
from app.services.face_matching_service import FaceMatchingService
from app.services.image_discovery_service import ImageDiscoveryService
from app.services.investigation_runner import EventBus, InvestigationRunner
from app.services.investigation_service import InvestigationService
from app.services.lead_service import LeadService
from app.services.page_analysis_service import PageAnalysisService
from app.services.reference_image_service import ReferenceImageService
from app.services.report_service import ReportService
from app.services.retention_service import RetentionService
from app.services.text_evidence_service import TextEvidenceService
from app.vision.face_embedder import build_face_backend

_UNSET: Any = object()


@dataclass
class Container:
    settings: Settings
    repo: InvestigationRepository
    cache: CacheStore
    api_client: httpx.AsyncClient
    fetcher: SafeFetcher
    sessions: SessionStore
    faces: FaceMatchingService
    reference_images: ReferenceImageService
    investigations: InvestigationService
    bus: EventBus
    runner: InvestigationRunner
    reports: ReportService
    retention: RetentionService
    reverse_providers: list[ReverseImageProvider]
    search_providers: list[WebSearchProvider]
    profile_providers: list[ProfileProvider]

    async def aclose(self) -> None:
        await self.runner.shutdown()
        await self.fetcher.aclose()
        await self.api_client.aclose()
        self.cache.close()
        self.repo.close()

    def provider_status(self) -> dict[str, Any]:
        return {
            "reverse_image": [{"name": p.name, "configured": p.is_configured()} for p in self.reverse_providers],
            "web_search": [{"name": p.name, "configured": p.is_configured()} for p in self.search_providers],
            "profiles": [{"name": p.name, "configured": p.is_configured()} for p in self.profile_providers],
            "face_matching": {
                "available": self.faces.matching_available,
                "detection_available": self.faces.detection_available,
                "model": self.faces.model_name,
                "reason": None if self.faces.matching_available else self.faces.unavailable_reason,
            },
        }


def build_container(
    settings: Settings | None = None,
    *,
    face_backend: Any = _UNSET,
    reverse_providers: list[ReverseImageProvider] | None = None,
    search_providers: list[WebSearchProvider] | None = None,
    profile_providers: list[ProfileProvider] | None = None,
    fetcher: SafeFetcher | None = None,
    api_client: httpx.AsyncClient | None = None,
    repo: InvestigationRepository | None = None,
    cache: CacheStore | None = None,
) -> Container:
    settings = settings or get_settings()
    ensure_data_dirs(settings)
    repo = repo or create_repository(settings.resolved_database_url)
    cache = cache or CacheStore(settings.data_dir / "cache.db")
    api_client = api_client or create_api_client(settings)
    sessions = SessionStore(settings)
    fetcher = fetcher or SafeFetcher(
        settings, cookie_provider=sessions.cookies_for_host if settings.session_management_enabled else None
    )
    if face_backend is _UNSET:
        backend, reason = build_face_backend(settings)
    else:
        backend, reason = (
            face_backend,
            (
                None
                if face_backend is not None and face_backend.supports_embeddings
                else "Face identity matching is unavailable."
            ),
        )
    faces = FaceMatchingService(settings, backend, cache, unavailable_reason=reason)
    if reverse_providers is None:
        reverse_providers = [
            GoogleCloudVisionWebDetectionProvider(settings, api_client),
            TinEyeProvider(settings, api_client),
        ]
    if search_providers is None:
        search_providers = [SearxngProvider(settings, api_client), DdgsProvider(settings)]
    if profile_providers is None:
        profile_providers = [
            GitHubProfileProvider(settings, api_client),
            WikidataProfileProvider(settings, api_client),
            GravatarProfileProvider(settings, api_client),
        ]
    reference_images = ReferenceImageService(settings, repo, faces)
    fusion = EvidenceFusionService()
    investigations = InvestigationService(
        settings=settings,
        repo=repo,
        reference_images=reference_images,
        image_discovery=ImageDiscoveryService(settings, reverse_providers, cache),
        candidate_discovery=CandidateDiscoveryService(settings, search_providers, profile_providers, cache),
        page_analysis=PageAnalysisService(settings, fetcher, cache),
        candidate_images=CandidateImageService(settings, fetcher, faces),
        faces=faces,
        text_evidence=TextEvidenceService(),
        clustering=CandidateClusteringService(faces),
        ranking=CandidateRankingService(fusion),
        leads=LeadService(settings, api_client),
    )
    bus = EventBus(repo)
    return Container(
        settings=settings,
        repo=repo,
        cache=cache,
        api_client=api_client,
        fetcher=fetcher,
        sessions=sessions,
        faces=faces,
        reference_images=reference_images,
        investigations=investigations,
        bus=bus,
        runner=InvestigationRunner(settings, investigations, repo, bus),
        reports=ReportService(),
        retention=RetentionService(settings, repo, cache),
        reverse_providers=reverse_providers,
        search_providers=search_providers,
        profile_providers=profile_providers,
    )
