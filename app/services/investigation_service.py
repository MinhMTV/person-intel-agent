"""The single investigation pipeline used by REST, SSE/live progress, CLI and UI.

reference images → reverse image discovery ┐
                                           ├→ candidate pages → page analysis → candidate images
identity hints   → parameter discovery ────┘        → faces/embeddings → face matching
→ text evidence → clustering → evidence fusion → ranking → leads → persisted result
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from app.config import PIPELINE_VERSION, Settings
from app.domain.candidate import CandidatePage, DiscoveryOrigin, PageProfile
from app.domain.evidence import Evidence, FaceEvidence, FaceMatchBand
from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.image import (
    CandidateImage,
    ImageDiscoveryResult,
    ImageMatchType,
    ImageOrigin,
    ReferenceImage,
    utcnow,
)
from app.domain.investigation import (
    EventType,
    Investigation,
    InvestigationResult,
    InvestigationStatus,
)
from app.infrastructure.persistence.repository import InvestigationRepository
from app.infrastructure.security.redaction import safe_error
from app.providers.profiles.base import ProfileRecord
from app.services.candidate_clustering_service import CandidateClusteringService, PageNode
from app.services.candidate_discovery_service import CandidateDiscoveryService
from app.services.candidate_image_service import CandidateImageService
from app.services.candidate_ranking_service import CandidateRankingService
from app.services.context import ProgressSink, RunContext
from app.services.evidence_builder import face_evidence, image_occurrence_evidence
from app.services.face_matching_service import BestFaceMatch, FaceMatchingService, ReferenceFace
from app.services.image_discovery_service import ImageDiscoveryOutcome, ImageDiscoveryService
from app.services.lead_service import LeadService
from app.services.page_analysis_service import PageAnalysisService
from app.services.reference_image_service import ReferenceImageService
from app.services.text_evidence_service import TextEvidenceService
from app.utils.canonical import canonical_image_url, canonical_url, domain_of, stable_hash
from app.utils.platforms import is_noise_domain, platform_for
from app.vision.image_hash import hamming_distance

logger = logging.getLogger(__name__)

_ORIGIN_PRIORITY = {
    DiscoveryOrigin.KNOWN_URL: 0,
    DiscoveryOrigin.REVERSE_IMAGE: 1,
    DiscoveryOrigin.PROFILE_PROVIDER: 2,
    DiscoveryOrigin.WEB_SEARCH: 3,
    DiscoveryOrigin.WEB_ENTITY_HINT: 4,
}


class InvestigationError(ValueError):
    pass


class PageRegistry:
    """Deduplicated candidate pages (by canonical URL) with a size budget."""

    def __init__(self, max_pages: int, max_image_only: int):
        self.max_pages = max_pages
        self.max_image_only = max_image_only
        self._pages: dict[str, CandidatePage] = {}

    @property
    def pages(self) -> list[CandidatePage]:
        return sorted(self._pages.values(), key=lambda p: min((_ORIGIN_PRIORITY[o] for o in p.origins), default=9))

    def add(
        self,
        url: str,
        origin: DiscoveryOrigin,
        provider: str | None = None,
        *,
        title: str | None = None,
        snippet: str | None = None,
        query: str | None = None,
        image_only: bool = False,
    ) -> CandidatePage | None:
        key = canonical_url(url)
        if not key.startswith("https://") or is_noise_domain(url):
            return None
        page = self._pages.get(key)
        if page is None:
            same_kind = sum(1 for p in self._pages.values() if p.is_image_only == image_only)
            if same_kind >= (self.max_image_only if image_only else self.max_pages):
                return None
            page = CandidatePage(
                id=stable_hash(key, 12),
                url=url,
                canonical_url=key,
                domain=domain_of(url),
                platform=platform_for(url),
                is_image_only=image_only,
            )
            self._pages[key] = page
        if origin not in page.origins:
            page.origins.append(origin)
        if provider and provider not in page.providers:
            page.providers.append(provider)
        if query and query not in page.queries:
            page.queries.append(query)
        page.title = page.title or title
        page.snippet = page.snippet or snippet
        return page

    @staticmethod
    def add_image(page: CandidatePage, url: str, origin: ImageOrigin, priority: float, provider: str | None) -> None:
        canon = canonical_image_url(url)
        if not canon.startswith("https://") or any(i.canonical_url == canon for i in page.images):
            return
        page.images.append(
            CandidateImage(
                id=stable_hash([page.id, canon], 12),
                url=url,
                canonical_url=canon,
                page_url=page.url,
                origin=origin,
                priority=priority,
                provider=provider,
            )
        )


@dataclass
class _State:
    registry: PageRegistry
    reverse: ImageDiscoveryOutcome = field(default_factory=ImageDiscoveryOutcome)
    face_matches: dict[str, BestFaceMatch] = field(default_factory=dict)  # canonical image url → match
    text_evidence: dict[str, list[Evidence]] = field(default_factory=dict)  # page id → evidence
    reference_copy_urls: set[str] = field(default_factory=set)  # images that ARE the reference photo


class InvestigationService:
    def __init__(
        self,
        settings: Settings,
        repo: InvestigationRepository,
        reference_images: ReferenceImageService,
        image_discovery: ImageDiscoveryService,
        candidate_discovery: CandidateDiscoveryService,
        page_analysis: PageAnalysisService,
        candidate_images: CandidateImageService,
        faces: FaceMatchingService,
        text_evidence: TextEvidenceService,
        clustering: CandidateClusteringService,
        ranking: CandidateRankingService,
        leads: LeadService,
    ):
        self.settings = settings
        self.repo = repo
        self.reference_images = reference_images
        self.image_discovery = image_discovery
        self.candidate_discovery = candidate_discovery
        self.page_analysis = page_analysis
        self.candidate_images = candidate_images
        self.faces = faces
        self.text_evidence = text_evidence
        self.clustering = clustering
        self.ranking = ranking
        self.leads = leads

    # ------------------------------------------------------------------ lifecycle
    def create(self, hints: IdentityHints | None = None, options: InvestigationOptions | None = None) -> Investigation:
        inv = Investigation(
            id=uuid.uuid4().hex[:16], hints=hints or IdentityHints(), options=options or InvestigationOptions()
        )
        self.repo.create(inv)
        return inv

    def get(self, investigation_id: str) -> Investigation | None:
        return self.repo.get(investigation_id)

    async def add_reference_image(self, investigation_id: str, filename: str | None, data: bytes) -> ReferenceImage:
        inv = self.repo.get(investigation_id, with_result=False)
        if inv is None:
            raise InvestigationError("Investigation not found")
        if inv.status.is_active:
            raise InvestigationError("Cannot change reference images while the investigation is running")
        return await self.reference_images.add(investigation_id, filename, data)

    def fingerprint(self, inv: Investigation, refs: list[ReferenceImage]) -> str:
        """Deterministic hash of every input that can affect the result."""
        return stable_hash(
            {
                "hints": inv.hints.normalized(),
                "options": inv.options.normalized(),
                "references": sorted((r.sha256, r.phash, r.selected_face_id or "") for r in refs),
                "reverse_providers": [p.cache_identity() for p in self.image_discovery.configured_providers()],
                "search_providers": [p.name for p in self.candidate_discovery.search_providers if p.is_configured()],
                "profile_providers": [p.name for p in self.candidate_discovery.profile_providers if p.is_configured()],
                "face_model": self.faces.model_name if self.faces.matching_available else None,
                "config": self.settings.config_fingerprint(),
            }
        )

    def validate_runnable(self, inv: Investigation) -> None:
        if not inv.reference_images and inv.hints.is_empty():
            raise InvestigationError("Upload at least one reference image or provide identity hints.")

    # ------------------------------------------------------------------- pipeline
    async def investigate(self, investigation_id: str, progress: ProgressSink | None = None) -> InvestigationResult:
        inv = self.repo.get(investigation_id, with_result=False)
        if inv is None:
            raise InvestigationError("Investigation not found")
        refs = self.repo.get_reference_images(investigation_id, with_embeddings=True)
        inv.reference_images = refs
        self.validate_runnable(inv)

        inv.fingerprint = self.fingerprint(inv, refs)
        inv.status = InvestigationStatus.RUNNING
        inv.error = None
        self.repo.update_meta(inv)
        ctx = RunContext(investigation_id, inv.hints, inv.options, sink=progress or (lambda *_: None))
        started = utcnow()
        t0 = time.perf_counter()
        try:
            ctx.emit(
                EventType.INVESTIGATION_STARTED,
                "Investigation started",
                reference_images=len(refs),
                hints_supplied=not inv.hints.is_empty(),
            )
            ref_faces = self._reference_stage(refs, ctx)
            state = _State(
                PageRegistry(
                    inv.options.max_candidate_pages or self.settings.max_candidate_pages,
                    self.settings.max_similar_images + 10,
                )
            )
            status = InvestigationStatus.COMPLETED
            try:
                await asyncio.wait_for(
                    self._collect(inv, refs, ref_faces, ctx, state), timeout=self.settings.investigation_timeout
                )
            except TimeoutError:
                status = InvestigationStatus.PARTIAL
                ctx.warn(
                    f"Investigation time limit ({self.settings.investigation_timeout}s) reached — "
                    "results are based on the sources processed so far."
                )

            nodes = self._page_nodes(state, refs, inv.hints)
            candidates = self.ranking.rank(self.clustering.cluster(nodes), inv.hints, self.faces.matching_available)
            for candidate in candidates[:25]:
                ctx.emit(
                    EventType.CANDIDATE_UPDATED,
                    f"{candidate.display_name}: {candidate.assessment.level.value}",
                    candidate_id=candidate.id,
                    name=candidate.display_name,
                    rank=candidate.rank,
                    level=candidate.assessment.level.value,
                    sources=len(candidate.urls),
                )
            pages = state.registry.pages
            try:
                leads = await asyncio.wait_for(self.leads.collect(inv.hints, pages, candidates), timeout=30)
            except Exception as exc:  # leads are optional extras
                ctx.warn(f"Lead collection skipped: {safe_error(exc)}")
                leads = []
            for page in pages:
                page.profile.text_excerpt = page.profile.text_excerpt[:1500]
                page.profile.outbound_links = page.profile.outbound_links[:50]
            ctx.stats.pages_discovered = len(pages)
            ctx.stats.candidate_count = len(candidates)
            ctx.stats.duration_ms = int((time.perf_counter() - t0) * 1000)
            result = InvestigationResult(
                investigation_id=investigation_id,
                status=status,
                pipeline_version=PIPELINE_VERSION,
                fingerprint=inv.fingerprint,
                started_at=started,
                completed_at=utcnow(),
                hints=inv.hints,
                reference_images=refs,
                reverse_image_results=state.reverse.results,
                web_entities=state.reverse.web_entities[:15],
                best_guess_labels=state.reverse.best_guess_labels,
                pages=pages,
                candidates=candidates,
                leads=leads,
                provider_runs=ctx.runs,
                stats=ctx.stats,
                warnings=ctx.warnings,
                conclusion=self.ranking.conclusion(candidates),
                face_matching_available=self.faces.matching_available,
                face_model=self.faces.model_name if self.faces.matching_available else None,
            )
            self.repo.save_result(result)
            inv.status = status
            self.repo.update_meta(inv)
            ctx.emit(
                EventType.INVESTIGATION_COMPLETED,
                result.conclusion,
                status=status.value,
                candidates=len(candidates),
                duration_ms=ctx.stats.duration_ms,
            )
            return result
        except asyncio.CancelledError:
            self.repo.set_status(investigation_id, InvestigationStatus.INTERRUPTED, "Cancelled")
            raise
        except Exception as exc:
            logger.exception("Investigation %s failed", investigation_id)
            message = safe_error(exc)
            self.repo.set_status(investigation_id, InvestigationStatus.FAILED, message)
            ctx.emit(EventType.INVESTIGATION_FAILED, "Investigation failed", error=message)
            raise

    # ----------------------------------------------------------------- stages
    def _reference_stage(self, refs: list[ReferenceImage], ctx: RunContext) -> list[ReferenceFace]:
        for ref in refs:
            ctx.emit(
                EventType.IMAGE_VALIDATED,
                f"Reference image {ref.width}×{ref.height} ({ref.quality.label.value})",
                image_id=ref.id,
                width=ref.width,
                height=ref.height,
                quality=ref.quality.label.value,
                issues=ref.quality.issues,
            )
            ctx.emit(
                EventType.FACE_DETECTED,
                f"{len(ref.faces)} face(s) detected",
                image_id=ref.id,
                faces=len(ref.faces),
                selected_face_id=ref.selected_face_id,
            )
            face = ref.selected_face
            if face is not None and face.embedding is not None:
                ctx.emit(
                    EventType.REFERENCE_EMBEDDING_CREATED,
                    f"{self.faces.model_name} embedding ready",
                    image_id=ref.id,
                    face_id=face.id,
                    model=self.faces.model_name,
                )
            if ref.quality.label.value == "POOR":
                ctx.warn(f"Reference image {ref.filename or ref.id}: quality POOR — matching may be unreliable.")
        ref_faces = self.faces.reference_faces(refs)
        if refs and not ref_faces:
            ctx.warn(
                (self.faces.unavailable_reason or "Face matching is unavailable.")
                if not self.faces.matching_available
                else "No target face embedding available (no face detected or embeddings expired); "
                "face matching is skipped."
            )
        return ref_faces

    async def _collect(
        self,
        inv: Investigation,
        refs: list[ReferenceImage],
        ref_faces: list[ReferenceFace],
        ctx: RunContext,
        state: _State,
    ) -> None:
        hints = inv.hints
        payloads: list[tuple[ReferenceImage, bytes]] = []
        for ref in refs:
            data = self.reference_images.read_bytes(ref)
            if data is not None:
                payloads.append((ref, data))
        if refs and not payloads:
            ctx.warn("Reference image files were removed by the retention policy; reverse image search is skipped.")

        async def reverse() -> ImageDiscoveryOutcome:
            if not inv.options.use_reverse_image or not payloads:
                return ImageDiscoveryOutcome()
            return await self.image_discovery.discover(payloads, ctx)

        state.reverse, discovery = await asyncio.gather(reverse(), self.candidate_discovery.discover(hints, ctx))
        entity_hits = []
        if not hints.name and self.settings.use_web_entity_hints and state.reverse.web_entities:
            entity_discovery = await self.candidate_discovery.discover(
                IdentityHints(location=hints.location, country=hints.country), ctx, entities=state.reverse.web_entities
            )
            entity_hits = entity_discovery.hits

        self._register_pages(state, hints, state.reverse, discovery.profiles, discovery.hits, entity_hits, ctx)

        # Page analysis (bounded concurrency)
        sem = asyncio.Semaphore(max(1, self.settings.page_fetch_concurrency))

        async def analyze(page: CandidatePage) -> None:
            async with sem:
                await self.page_analysis.analyze(page, ctx)
            state.text_evidence[page.id] = self.text_evidence.evaluate(page, hints)
            if state.text_evidence[page.id]:
                ctx.emit(
                    EventType.TEXT_EVIDENCE_FOUND,
                    f"Identity hints matched on {page.domain}",
                    page_id=page.id,
                    url=page.url,
                    types=sorted({e.type.value for e in state.text_evidence[page.id]}),
                )

        await asyncio.gather(*(analyze(p) for p in state.registry.pages))

        # Candidate images: download once, dedupe, faces + embeddings
        images = [img for page in state.registry.pages for img in page.images]
        await self.candidate_images.process(images, ctx)
        self._match_faces(state, refs, ref_faces, ctx)

    def _register_pages(
        self,
        state: _State,
        hints: IdentityHints,
        reverse: ImageDiscoveryOutcome,
        profiles: list[ProfileRecord],
        hits: list,
        entity_hits: list,
        ctx: RunContext,
    ) -> None:
        registry = state.registry
        for url in hints.known_urls:
            registry.add(url, DiscoveryOrigin.KNOWN_URL, "investigator")
        similar = 0
        for result in reverse.results:
            if result.match_type != ImageMatchType.VISUALLY_SIMILAR and result.image_url:
                state.reference_copy_urls.add(canonical_image_url(result.image_url))
            if result.page_url:
                page = registry.add(
                    result.page_url, DiscoveryOrigin.REVERSE_IMAGE, result.provider, title=result.page_title
                )
                if page is None:
                    continue
                page.image_hits.append(result)
                if result.image_url:
                    registry.add_image(page, result.image_url, ImageOrigin.PROVIDER_MATCH, 1.0, result.provider)
            elif result.image_url:
                if result.match_type == ImageMatchType.VISUALLY_SIMILAR:
                    if similar >= self.settings.max_similar_images:
                        continue
                    similar += 1
                page = registry.add(
                    result.image_url,
                    DiscoveryOrigin.REVERSE_IMAGE,
                    result.provider,
                    title=f"Image on {domain_of(result.image_url)}",
                    image_only=True,
                )
                if page is None:
                    continue
                page.image_hits.append(result)
                registry.add_image(page, result.image_url, ImageOrigin.PROVIDER_MATCH, 1.0, result.provider)
        for record in profiles:
            page = registry.add(
                record.url,
                DiscoveryOrigin.PROFILE_PROVIDER,
                record.provider,
                title=record.display_name,
                snippet=record.bio,
                query=record.lookup,
            )
            if page is None:
                continue
            self._apply_profile(page, record)
            if record.avatar_url:
                registry.add_image(page, record.avatar_url, ImageOrigin.PROFILE_API, 0.95, record.provider)
        for hit in hits:
            page = registry.add(
                hit.url, DiscoveryOrigin.WEB_SEARCH, hit.provider, title=hit.title, snippet=hit.snippet, query=hit.query
            )
            if page is not None and hit.image_url:
                registry.add_image(page, hit.image_url, ImageOrigin.INLINE, 0.5, hit.provider)
        for hit in entity_hits:
            registry.add(
                hit.url,
                DiscoveryOrigin.WEB_ENTITY_HINT,
                hit.provider,
                title=hit.title,
                snippet=hit.snippet,
                query=hit.query,
            )
        for page in registry.pages:
            ctx.emit(
                EventType.CANDIDATE_PAGE_DISCOVERED,
                page.title or page.domain,
                page_id=page.id,
                url=page.url,
                origins=[o.value for o in page.origins],
                providers=page.providers,
            )

    @staticmethod
    def _apply_profile(page: CandidatePage, record: ProfileRecord) -> None:
        profile: PageProfile = page.profile
        profile.profile_name = profile.profile_name or record.display_name
        if record.username:
            profile.usernames.append(record.username.lower())
        if record.location:
            profile.locations.append(record.location)
        if record.organization:
            profile.organizations.append(record.organization)
        profile.emails += record.emails
        profile.social_links += [u for u in [record.website, *record.linked_urls] if u]
        profile.text_excerpt = " \n".join(
            x for x in [record.display_name, record.bio, record.location, record.organization, record.website] if x
        )

    def _match_faces(
        self, state: _State, refs: list[ReferenceImage], ref_faces: list[ReferenceFace], ctx: RunContext
    ) -> None:
        if not ref_faces:
            return
        ref_hashes = [r.phash for r in refs]
        for page in state.registry.pages:
            for image in page.images:
                if image.phash and any(
                    hamming_distance(image.phash, h) <= self.settings.phash_duplicate_distance for h in ref_hashes
                ):
                    state.reference_copy_urls.add(image.canonical_url)
                if image.canonical_url in state.face_matches or not image.faces:
                    continue
                match = self.faces.find_best_face_match(ref_faces, image.faces)
                if match is None:
                    continue
                ctx.stats.face_comparisons += len(image.faces) * len(ref_faces)
                state.face_matches[image.canonical_url] = match
                if match.comparison.band.rank >= FaceMatchBand.MEDIUM.rank:
                    ctx.emit(
                        EventType.FACE_MATCH_FOUND,
                        f"Face similarity {match.comparison.band.value} on {page.domain}",
                        url=image.url,
                        page_url=page.url,
                        band=match.comparison.band.value,
                        cosine_similarity=match.comparison.cosine_similarity,
                        reference_copy=image.canonical_url in state.reference_copy_urls,
                    )

    def _page_nodes(self, state: _State, refs: list[ReferenceImage], hints: IdentityHints) -> list[PageNode]:
        ref_hashes = [r.phash for r in refs]
        model = self.faces.model_name or "unknown"
        nodes: list[PageNode] = []
        for page in state.registry.pages:
            evidence: list[Evidence] = []
            source = page.url
            for hit in page.image_hits:
                evidence.append(image_occurrence_evidence(hit, source))
            hit_images = {canonical_image_url(h.image_url) for h in page.image_hits if h.image_url}
            best: tuple[FaceEvidence, list[float] | None] | None = None
            weak_face: FaceEvidence | None = None
            for image in page.images:
                is_copy = image.canonical_url in state.reference_copy_urls or (
                    image.phash is not None
                    and any(
                        hamming_distance(image.phash, h) <= self.settings.phash_duplicate_distance for h in ref_hashes
                    )
                )
                if is_copy and image.canonical_url not in hit_images:
                    evidence.append(
                        image_occurrence_evidence(
                            ImageDiscoveryResult(
                                provider="local_phash",
                                match_type=ImageMatchType.EXACT,
                                image_url=image.url,
                                page_url=page.url if not page.is_image_only else None,
                                reference_image_id=refs[0].id if refs else None,
                            ),
                            source,
                        )
                    )
                match = state.face_matches.get(image.canonical_url)
                if match is None or is_copy:
                    # A copy of the reference photo trivially matches its own face;
                    # that is image-occurrence evidence, not independent face evidence.
                    continue
                fe = FaceEvidence(
                    model=model,
                    distance=match.comparison.distance,
                    cosine_similarity=match.comparison.cosine_similarity,
                    match_band=match.comparison.band,
                    reference_image_id=match.reference.reference_image_id,
                    reference_face_id=match.reference.face_id,
                    references_compared=match.comparison.references_compared,
                    candidate_image_url=image.url,
                    candidate_face_id=match.face.id,
                    candidate_bbox=match.face.bbox,
                    candidate_thumbnail=match.face.thumbnail,
                )
                if fe.match_band.rank >= FaceMatchBand.MEDIUM.rank:
                    evidence.append(face_evidence(fe, source))
                elif weak_face is None or fe.distance < weak_face.distance:
                    weak_face = fe
                if best is None or fe.distance < best[0].distance:
                    best = (fe, match.face.embedding)
            if weak_face is not None and not any(e.face for e in evidence):
                evidence.append(face_evidence(weak_face, source))  # counter-evidence: compared, did not match
            if page.id in state.text_evidence:
                evidence.extend(state.text_evidence[page.id])
            else:  # page not reached before the time limit
                evidence.extend(self.text_evidence.evaluate(page, hints))
            nodes.append(
                PageNode(
                    page=page,
                    evidence=evidence,
                    best_face=best[0] if best else None,
                    best_face_embedding=np.asarray(best[1]) if best and best[1] is not None else None,
                )
            )
        return nodes

    # ---------------------------------------------------------------- listing
    def list_investigations(
        self, *, limit: int = 50, tag: str | None = None, query: str | None = None
    ) -> list[Investigation]:
        return self.repo.list_investigations(limit=limit, tag=tag, query=query)
