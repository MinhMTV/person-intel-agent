"""Reference image pipeline: validate → normalise → hash → detect → embed → quality."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

import numpy as np

from app.config import Settings
from app.domain.image import BoundingBox, DetectedFace, ReferenceImage
from app.infrastructure.persistence.repository import InvestigationRepository
from app.services.face_matching_service import FaceMatchingService
from app.vision.face_detector import FaceObservation
from app.vision.image_hash import phash
from app.vision.image_quality import assess_quality, face_region_stats
from app.vision.image_validation import ValidatedImage, validate_image_bytes
from app.vision.thumbnails import face_thumbnail

MAX_STORED_SIDE = 2048


class ReferenceImageError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_display_name(filename: str | None) -> str:
    base = os.path.basename(filename or "")
    return re.sub(r"[^\w.\- ]", "_", base)[:80]


def _face_score(face: FaceObservation, blur: float) -> float:
    """Default target choice: large + sharp + confidently detected."""
    confidence = face.confidence if face.confidence is not None else 0.8
    sharpness = min(1.0, 0.3 + blur / 150.0)
    return (face.w * face.h) * (0.4 + confidence) * sharpness


class ReferenceImageService:
    def __init__(self, settings: Settings, repo: InvestigationRepository, faces: FaceMatchingService):
        self.settings = settings
        self.repo = repo
        self.faces = faces

    async def add(self, investigation_id: str, filename: str | None, data: bytes) -> ReferenceImage:
        existing = self.repo.get_reference_images(investigation_id)
        if len(existing) >= self.settings.max_reference_images:
            raise ReferenceImageError(
                "too_many", f"At most {self.settings.max_reference_images} reference images are allowed."
            )
        validated: ValidatedImage = await asyncio.to_thread(
            validate_image_bytes,
            data,
            max_bytes=self.settings.max_upload_bytes,
            max_pixels=self.settings.max_image_pixels,
            min_dimension=self.settings.min_image_dimension,
            max_dimension=self.settings.max_image_dimension,
        )
        if any(r.sha256 == validated.original_sha256 for r in existing):
            raise ReferenceImageError("duplicate", "This image was already added to the investigation.")

        rgb = np.asarray(validated.image)
        observations = await self.faces.detect_and_embed(rgb)
        detected: list[DetectedFace] = []
        scores: dict[str, float] = {}
        for index, obs in enumerate(sorted(observations, key=lambda f: f.w * f.h, reverse=True)):
            blur, _brightness, roll = face_region_stats(rgb, obs)
            face_id = f"f{index + 1}"
            detected.append(
                DetectedFace(
                    id=face_id,
                    bbox=BoundingBox(x=obs.x, y=obs.y, w=obs.w, h=obs.h),
                    detector_confidence=obs.confidence,
                    embedding=[float(v) for v in obs.embedding] if obs.embedding is not None else None,
                    thumbnail=face_thumbnail(validated.image, obs.x, obs.y, obs.w, obs.h),
                    blur_variance=round(blur, 1),
                    area_ratio=round(obs.w * obs.h / float(validated.width * validated.height), 4),
                    roll_degrees=roll,
                )
            )
            scores[face_id] = _face_score(obs, blur)
        selected = max(scores, key=lambda k: scores[k]) if scores else None
        target_obs = None
        if selected is not None:
            face = next(f for f in detected if f.id == selected)
            target_obs = FaceObservation(face.bbox.x, face.bbox.y, face.bbox.w, face.bbox.h, face.detector_confidence)
        quality = assess_quality(rgb, observations, target_obs)

        warnings = list(quality.issues)
        if detected and not self.faces.matching_available:
            warnings.append(self.faces.unavailable_reason or "Face identity matching is unavailable.")

        image_id = uuid.uuid4().hex[:16]
        stored = self._store(image_id, validated)
        ref = ReferenceImage(
            id=image_id,
            investigation_id=investigation_id,
            filename=_safe_display_name(filename),
            mime=validated.mime,
            size_bytes=validated.size_bytes,
            width=validated.width,
            height=validated.height,
            sha256=validated.original_sha256,
            phash=phash(validated.image),
            faces=detected,
            selected_face_id=selected,
            quality=quality,
            warnings=warnings,
            stored_path=str(stored),
        )
        self.repo.save_reference_image(ref)
        return ref

    def _store(self, image_id: str, validated: ValidatedImage) -> Path:
        """Store the normalised (EXIF-free) JPEG with owner-only permissions."""
        self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.upload_dir / f"{image_id}.jpg"
        data = validated.to_jpeg(max_side=MAX_STORED_SIDE)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path

    def select_face(self, investigation_id: str, image_id: str, face_id: str) -> ReferenceImage:
        refs = self.repo.get_reference_images(investigation_id)
        ref = next((r for r in refs if r.id == image_id), None)
        if ref is None:
            raise ReferenceImageError("not_found", "Reference image not found.")
        if not any(f.id == face_id for f in ref.faces):
            raise ReferenceImageError("invalid_face", "Unknown face id.")
        ref.selected_face_id = face_id
        self.repo.save_reference_image(ref)  # embeddings are kept by the repository
        return ref

    def delete(self, investigation_id: str, image_id: str) -> bool:
        ref = self.repo.delete_reference_image(investigation_id, image_id)
        if ref is None:
            return False
        self._unlink(ref.stored_path)
        return True

    def read_bytes(self, ref: ReferenceImage) -> bytes | None:
        path = self.settings.upload_path(ref.stored_path)  # never read outside the upload directory
        return path.read_bytes() if path and path.exists() else None

    def _unlink(self, stored_path: str | None) -> None:
        path = self.settings.upload_path(stored_path)
        if path:
            path.unlink(missing_ok=True)
