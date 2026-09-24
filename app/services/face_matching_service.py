"""Local face identity matching.

Measurement: cosine distance between embeddings of the single configured
model (default ArcFace). The raw distance and cosine similarity are kept;
they are mapped to descriptive bands. **Bands are NOT probabilities.**

Default ArcFace bands (cosine distance, configurable via env):

    distance ≤ 0.40  VERY_HIGH   (FACE_VERY_HIGH_THRESHOLD)
    distance ≤ 0.55  HIGH        (FACE_HIGH_THRESHOLD)
    distance ≤ 0.68  MEDIUM      (FACE_MATCH_THRESHOLD — DeepFace's ArcFace verification threshold)
    distance ≤ 0.80  LOW         (FACE_LOW_THRESHOLD)
    otherwise        NO_MATCH

0.68 is the cut-off DeepFace publishes for ArcFace+cosine. The tighter
HIGH/VERY_HIGH bands are conservative heuristics chosen so that a HIGH band
needs clearly more similarity than the minimal verification threshold.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np

from app.config import Settings
from app.domain.evidence import FaceMatchBand
from app.domain.image import DetectedFace, ReferenceImage
from app.infrastructure.cache.store import CacheStore, cache_key
from app.vision.face_detector import FaceBackend, FaceObservation


@dataclass
class FaceComparison:
    distance: float
    cosine_similarity: float
    band: FaceMatchBand
    references_compared: int = 1


@dataclass
class ReferenceFace:
    reference_image_id: str
    face_id: str
    embedding: np.ndarray


@dataclass
class BestFaceMatch:
    face: DetectedFace
    comparison: FaceComparison
    reference: ReferenceFace


def cosine_distance(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 1.0, 0.0
    similarity = float(np.dot(a, b) / denom)
    return 1.0 - similarity, similarity


class FaceMatchingService:
    def __init__(self, settings: Settings, backend: FaceBackend | None, cache: CacheStore | None = None,
                 unavailable_reason: str | None = None):
        self.settings = settings
        self.backend = backend
        self.cache = cache
        self.unavailable_reason = unavailable_reason
        self._semaphore = asyncio.Semaphore(max(1, settings.face_worker_concurrency))

    # ------------------------------------------------------------------ status
    @property
    def detection_available(self) -> bool:
        return self.backend is not None

    @property
    def matching_available(self) -> bool:
        return self.backend is not None and self.backend.supports_embeddings

    @property
    def model_name(self) -> str | None:
        return self.backend.model if self.backend is not None else None

    # ------------------------------------------------------------- embeddings
    async def detect_and_embed(self, rgb: np.ndarray) -> list[FaceObservation]:
        if self.backend is None:
            return []
        async with self._semaphore:
            return await asyncio.to_thread(self.backend.analyze, rgb)

    async def create_embedding(self, image_sha256: str, rgb: np.ndarray) -> tuple[list[FaceObservation], bool]:
        """Faces + embeddings for a (candidate) image, cached by content hash.

        Returns ``(faces, cache_hit)``.
        """
        key = None
        if self.cache is not None and self.backend is not None:
            key = cache_key(
                "face_embedding", sha256=image_sha256, backend=self.backend.name, model=self.backend.model,
                detector=self.settings.face_detector,
            )
            cached = self.cache.get("face_embedding", key)
            if cached is not None:
                return [
                    FaceObservation(
                        x=f["x"], y=f["y"], w=f["w"], h=f["h"], confidence=f.get("c"),
                        embedding=np.asarray(f["e"], dtype=np.float32) if f.get("e") is not None else None,
                    )
                    for f in cached
                ], True
        faces = await self.detect_and_embed(rgb)
        if key is not None and self.cache is not None:
            self.cache.set(
                "face_embedding",
                key,
                [
                    {"x": f.x, "y": f.y, "w": f.w, "h": f.h, "c": f.confidence,
                     "e": [round(float(v), 6) for v in f.embedding] if f.embedding is not None else None}
                    for f in faces
                ],
                self.settings.cache_ttl_face_embedding,
            )
        return faces, False

    # ------------------------------------------------------------- comparison
    def band_for(self, distance: float) -> FaceMatchBand:
        s = self.settings
        if distance <= s.face_very_high_threshold:
            return FaceMatchBand.VERY_HIGH
        if distance <= s.face_high_threshold:
            return FaceMatchBand.HIGH
        if distance <= s.face_match_threshold:
            return FaceMatchBand.MEDIUM
        if distance <= s.face_low_threshold:
            return FaceMatchBand.LOW
        return FaceMatchBand.NO_MATCH

    def compare(self, embedding_a: np.ndarray | list[float], embedding_b: np.ndarray | list[float]) -> FaceComparison:
        distance, similarity = cosine_distance(np.asarray(embedding_a), np.asarray(embedding_b))
        return FaceComparison(round(distance, 4), round(similarity, 4), self.band_for(distance))

    def compare_multiple_references(
        self, references: list[ReferenceFace], candidate_embedding: np.ndarray | list[float]
    ) -> tuple[FaceComparison, ReferenceFace] | None:
        """Aggregate over several reference photos of the same person.

        With 1–2 references the closest distance is used. With ≥3 the
        *second*-closest distance is used, so one lucky match against a single
        reference cannot dominate (robust against outliers).
        """
        if not references:
            return None
        scored = sorted(
            ((self.compare(ref.embedding, candidate_embedding), ref) for ref in references),
            key=lambda item: item[0].distance,
        )
        best_cmp, best_ref = scored[0]
        used = scored[1][0] if len(scored) >= 3 else best_cmp
        comparison = FaceComparison(used.distance, used.cosine_similarity, used.band, references_compared=len(scored))
        return comparison, best_ref

    def find_best_face_match(self, references: list[ReferenceFace], faces: list[DetectedFace]) -> BestFaceMatch | None:
        """Compare EVERY candidate face (not just the largest) and keep the best."""
        best: BestFaceMatch | None = None
        for face in faces:
            if face.embedding is None:
                continue
            result = self.compare_multiple_references(references, face.embedding)
            if result is None:
                continue
            comparison, ref = result
            if best is None or comparison.distance < best.comparison.distance:
                best = BestFaceMatch(face=face, comparison=comparison, reference=ref)
        return best

    @staticmethod
    def reference_faces(references: list[ReferenceImage]) -> list[ReferenceFace]:
        out = []
        for ref in references:
            face = ref.selected_face
            if face is not None and face.embedding is not None:
                out.append(ReferenceFace(ref.id, face.id, np.asarray(face.embedding, dtype=np.float32)))
        return out
