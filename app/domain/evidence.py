"""First-class evidence model.

Every ``Evidence`` item is an *observation* ("page X contains the reference
photo", "face on image Y has cosine similarity 0.61 to the target face").
Conclusions live in :class:`app.domain.candidate.Assessment`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.domain.image import BoundingBox, ImageDiscoveryResult, utcnow


class EvidenceType(str, Enum):
    EXACT_IMAGE = "EXACT_IMAGE"
    PARTIAL_IMAGE = "PARTIAL_IMAGE"
    SIMILAR_IMAGE = "SIMILAR_IMAGE"
    FACE_SIMILARITY = "FACE_SIMILARITY"
    NAME_MATCH = "NAME_MATCH"
    USERNAME_MATCH = "USERNAME_MATCH"
    LOCATION_MATCH = "LOCATION_MATCH"
    EMAIL_MATCH = "EMAIL_MATCH"
    EMPLOYER_MATCH = "EMPLOYER_MATCH"
    EDUCATION_MATCH = "EDUCATION_MATCH"
    PROFESSION_MATCH = "PROFESSION_MATCH"
    KNOWN_URL = "KNOWN_URL"
    CROSS_LINK = "CROSS_LINK"


class EvidenceStrength(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"


class FaceMatchBand(str, Enum):
    VERY_HIGH = "VERY_HIGH"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NO_MATCH = "NO_MATCH"

    @property
    def rank(self) -> int:
        return {"VERY_HIGH": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NO_MATCH": 0}[self.value]


class FaceEvidence(BaseModel):
    model: str
    distance_metric: str = "cosine"
    distance: float
    cosine_similarity: float
    match_band: FaceMatchBand
    reference_image_id: str
    reference_face_id: str
    references_compared: int = 1
    candidate_image_url: str
    candidate_face_id: str
    candidate_bbox: BoundingBox | None = None
    candidate_thumbnail: str | None = None


class TextMatch(BaseModel):
    field: str  # name, location, username, …
    expected: str  # the hint supplied by the user
    observed: str  # what was found on the page (short excerpt)
    exact: bool = True


class Evidence(BaseModel):
    id: str
    type: EvidenceType
    strength: EvidenceStrength
    observation: str
    source_url: str | None = None
    source_domain: str | None = None
    provider: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    face: FaceEvidence | None = None
    image: ImageDiscoveryResult | None = None
    text: TextMatch | None = None
    related_url: str | None = None  # e.g. the other end of a cross-link

    def dedupe_key(self) -> tuple[str, str, str]:
        detail = ""
        if self.face:
            detail = f"{self.face.candidate_image_url}#{self.face.candidate_face_id}"
        elif self.image:
            detail = f"{self.image.image_url}|{self.image.match_type.value}"
        elif self.text:
            detail = f"{self.text.field}:{self.text.expected.lower()}"
        elif self.related_url:
            detail = self.related_url
        return (self.type.value, self.source_url or "", detail)
