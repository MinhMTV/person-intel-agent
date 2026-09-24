"""Download candidate images once, deduplicate them and detect/embed faces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import numpy as np

from app.config import Settings
from app.domain.image import BoundingBox, CandidateImage, CandidateImageStatus, DetectedFace, utcnow
from app.domain.investigation import EventType
from app.infrastructure.http.safe_fetcher import FetchError, SafeFetcher
from app.services.context import RunContext
from app.services.face_matching_service import FaceMatchingService
from app.utils.canonical import stable_hash
from app.vision.image_hash import hamming_distance, phash
from app.vision.image_quality import face_region_stats
from app.vision.image_validation import ImageValidationError, validate_image_bytes
from app.vision.thumbnails import face_thumbnail

_NEAR_DUPLICATE = 2  # pHash bits: same picture re-encoded / resized


@dataclass
class _Processed:
    status: CandidateImageStatus
    error: str | None = None
    sha256: str | None = None
    phash: str | None = None
    width: int | None = None
    height: int | None = None
    faces: list[DetectedFace] = field(default_factory=list)
    duplicate_of: str | None = None


class CandidateImageService:
    def __init__(self, settings: Settings, fetcher: SafeFetcher, faces: FaceMatchingService):
        self.settings = settings
        self.fetcher = fetcher
        self.faces = faces

    async def process(self, images: list[CandidateImage], ctx: RunContext) -> None:
        """Mutates ``images`` in place. The same canonical URL is fetched once."""
        by_url: dict[str, list[CandidateImage]] = {}
        for image in images:
            by_url.setdefault(image.canonical_url, []).append(image)
        ctx.stats.images_considered += len(by_url)
        sem = asyncio.Semaphore(max(1, self.settings.image_fetch_concurrency))
        by_sha: dict[str, _Processed] = {}
        by_phash: list[tuple[str, _Processed, str]] = []
        lock = asyncio.Lock()

        async def handle(canon: str, group: list[CandidateImage]) -> None:
            async with sem:
                processed = await self._process_one(group[0].url, canon, by_sha, by_phash, lock, ctx)
            for image in group:
                image.status = processed.status
                image.error = processed.error
                image.sha256 = processed.sha256
                image.phash = processed.phash
                image.width, image.height = processed.width, processed.height
                image.faces = list(processed.faces)
                image.duplicate_of = processed.duplicate_of
                image.retrieved_at = utcnow()

        await asyncio.gather(*(handle(canon, group) for canon, group in by_url.items()))

    async def _process_one(
        self,
        url: str,
        canon: str,
        by_sha: dict[str, _Processed],
        by_phash: list[tuple[str, _Processed, str]],
        lock: asyncio.Lock,
        ctx: RunContext,
    ) -> _Processed:
        try:
            fetched = await self.fetcher.fetch(url, kind="image")
        except FetchError as exc:
            return _Processed(CandidateImageStatus.FAILED, error=f"{exc.code}: {exc.message}"[:160])
        try:
            validated = await asyncio.to_thread(
                validate_image_bytes, fetched.content, max_bytes=self.settings.max_remote_image_bytes,
                max_pixels=self.settings.max_image_pixels, min_dimension=48,
                max_dimension=self.settings.max_image_dimension,
            )
        except ImageValidationError as exc:
            return _Processed(CandidateImageStatus.FAILED, error=exc.code)
        ctx.stats.images_downloaded += 1
        image_phash = phash(validated.image)

        async with lock:  # cheap duplicate checks before expensive face processing
            existing = by_sha.get(validated.original_sha256)
            if existing is None:
                for other_phash, other, _url in by_phash:
                    if hamming_distance(image_phash, other_phash) <= _NEAR_DUPLICATE:
                        existing = other
                        break
            if existing is not None:
                ctx.stats.images_deduplicated += 1
                return _Processed(
                    CandidateImageStatus.DUPLICATE if existing.status != CandidateImageStatus.FAILED else existing.status,
                    sha256=validated.original_sha256, phash=image_phash, width=validated.width,
                    height=validated.height, faces=existing.faces, duplicate_of=existing.sha256,
                )
            placeholder = _Processed(CandidateImageStatus.PENDING, sha256=validated.original_sha256, phash=image_phash)
            by_sha[validated.original_sha256] = placeholder
            by_phash.append((image_phash, placeholder, canon))

        rgb = np.asarray(validated.image)
        observations, cache_hit = await self.faces.create_embedding(validated.original_sha256, rgb)
        ctx.cache(cache_hit)
        faces = []
        for index, obs in enumerate(observations):
            blur, _b, roll = face_region_stats(rgb, obs)
            faces.append(
                DetectedFace(
                    id=f"{stable_hash([validated.original_sha256], 8)}-{index + 1}",
                    bbox=BoundingBox(x=obs.x, y=obs.y, w=obs.w, h=obs.h),
                    detector_confidence=obs.confidence,
                    embedding=[float(v) for v in obs.embedding] if obs.embedding is not None else None,
                    thumbnail=face_thumbnail(validated.image, obs.x, obs.y, obs.w, obs.h, size=80),
                    blur_variance=round(blur, 1),
                    area_ratio=round(obs.w * obs.h / float(validated.width * validated.height), 4),
                    roll_degrees=roll,
                )
            )
        ctx.stats.faces_detected += len(faces)
        placeholder.status = CandidateImageStatus.ANALYZED if faces else CandidateImageStatus.NO_FACE
        placeholder.faces = faces
        placeholder.width, placeholder.height = validated.width, validated.height
        ctx.emit(EventType.CANDIDATE_IMAGE_DOWNLOADED, f"Image analysed: {len(faces)} face(s)",
                 url=url, faces=len(faces))
        return placeholder

