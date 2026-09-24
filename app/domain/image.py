"""Image-related domain objects: reference images, candidate images, faces."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class QualityLabel(str, Enum):
    GOOD = "GOOD"
    USABLE = "USABLE"
    POOR = "POOR"


class ImageMatchType(str, Enum):
    """How a discovered image relates to the reference image (not the person)."""

    EXACT = "EXACT"  # same image (possibly resized / recompressed)
    MODIFIED = "MODIFIED"  # same image with edits (colour, overlays, …)
    PARTIAL = "PARTIAL"  # cropped / partially matching
    VISUALLY_SIMILAR = "VISUALLY_SIMILAR"  # different image that looks alike


class ImageOrigin(str, Enum):
    """Why a candidate image was selected for download."""

    PROVIDER_MATCH = "PROVIDER_MATCH"  # returned by a reverse-image provider
    PROFILE_API = "PROFILE_API"  # avatar from a structured profile API
    OPEN_GRAPH = "OPEN_GRAPH"
    STRUCTURED_DATA = "STRUCTURED_DATA"
    AVATAR = "AVATAR"
    INLINE = "INLINE"


class BoundingBox(BaseModel):
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)


class ImageQuality(BaseModel):
    label: QualityLabel
    width: int
    height: int
    face_count: int
    face_box_ratio: float | None = None  # target-face area / image area
    face_min_side: int | None = None  # px of the smaller face-box side
    blur_variance: float | None = None  # Laplacian variance of face region
    brightness: float | None = None
    roll_degrees: float | None = None  # head tilt estimated from eye landmarks
    issues: list[str] = Field(default_factory=list)


class DetectedFace(BaseModel):
    id: str
    bbox: BoundingBox
    detector_confidence: float | None = None
    # Embeddings are biometric data: never serialised in API responses/exports.
    embedding: list[float] | None = Field(default=None, exclude=True, repr=False)
    thumbnail: str | None = None  # small JPEG data URI for the UI
    blur_variance: float | None = None
    area_ratio: float | None = None
    roll_degrees: float | None = None


class ReferenceImage(BaseModel):
    id: str
    investigation_id: str
    filename: str = ""
    mime: str
    size_bytes: int
    width: int
    height: int
    sha256: str
    phash: str
    faces: list[DetectedFace] = Field(default_factory=list)
    selected_face_id: str | None = None
    quality: ImageQuality
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    image_available: bool = True  # False once retention purged the stored file
    stored_path: str | None = Field(default=None, exclude=True, repr=False)

    @property
    def selected_face(self) -> DetectedFace | None:
        for face in self.faces:
            if face.id == self.selected_face_id:
                return face
        return None


class ImageDiscoveryResult(BaseModel):
    """One observation from a reverse-image provider (photo-level, not identity)."""

    provider: str
    match_type: ImageMatchType
    image_url: str | None = None
    page_url: str | None = None
    page_title: str | None = None
    provider_score: float | None = None
    reference_image_id: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)


class CandidateImageStatus(str, Enum):
    PENDING = "PENDING"
    ANALYZED = "ANALYZED"
    NO_FACE = "NO_FACE"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"


class CandidateImage(BaseModel):
    id: str
    url: str
    canonical_url: str
    page_url: str | None = None
    origin: ImageOrigin
    priority: float = 0.0
    provider: str | None = None
    status: CandidateImageStatus = CandidateImageStatus.PENDING
    error: str | None = None
    sha256: str | None = None
    phash: str | None = None
    width: int | None = None
    height: int | None = None
    faces: list[DetectedFace] = Field(default_factory=list)
    duplicate_of: str | None = None
    retrieved_at: datetime | None = None
