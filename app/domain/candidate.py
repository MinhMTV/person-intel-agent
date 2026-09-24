"""Candidate pages and candidate identities."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.domain.evidence import Evidence, FaceEvidence, FaceMatchBand
from app.domain.image import CandidateImage, ImageDiscoveryResult, ImageMatchType


class DiscoveryOrigin(str, Enum):
    REVERSE_IMAGE = "REVERSE_IMAGE"
    WEB_SEARCH = "WEB_SEARCH"
    PROFILE_PROVIDER = "PROFILE_PROVIDER"
    KNOWN_URL = "KNOWN_URL"
    WEB_ENTITY_HINT = "WEB_ENTITY_HINT"


class PageProfile(BaseModel):
    """Structured facts extracted from a candidate page (observations only)."""

    fetched: bool = False
    fetch_error: str | None = None
    status_code: int | None = None
    final_url: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    description: str | None = None
    site_name: str | None = None
    profile_name: str | None = None
    usernames: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    social_links: list[str] = Field(default_factory=list)
    outbound_links: list[str] = Field(default_factory=list)
    text_excerpt: str = ""  # bounded text used for identity matching


class CandidatePage(BaseModel):
    id: str
    url: str
    canonical_url: str
    domain: str
    platform: str | None = None
    title: str | None = None
    snippet: str | None = None
    # True when only an image URL is known (e.g. a provider "full matching
    # image" without a containing page). Such entries are never page-fetched.
    is_image_only: bool = False
    origins: list[DiscoveryOrigin] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    image_hits: list[ImageDiscoveryResult] = Field(default_factory=list)
    profile: PageProfile = Field(default_factory=PageProfile)
    images: list[CandidateImage] = Field(default_factory=list)


class EvidenceLevel(str, Enum):
    VERY_STRONG = "VERY_STRONG"
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    INSUFFICIENT = "INSUFFICIENT"

    @property
    def rank(self) -> int:
        return {"VERY_STRONG": 4, "STRONG": 3, "MODERATE": 2, "WEAK": 1, "INSUFFICIENT": 0}[self.value]


class MatchState(str, Enum):
    YES = "YES"
    PARTIAL = "PARTIAL"
    NO = "NO"  # hint supplied, not observed on any source of this candidate
    UNKNOWN = "UNKNOWN"  # hint not supplied


class AssessmentDimensions(BaseModel):
    """Interpretable 0..1 sub-scores. NOT probabilities."""

    image_occurrence_strength: float = 0.0
    face_match_strength: float = 0.0
    identity_text_strength: float = 0.0
    cross_source_strength: float = 0.0
    source_quality: float = 0.0


class Assessment(BaseModel):
    level: EvidenceLevel = EvidenceLevel.INSUFFICIENT
    score: float = 0.0  # internal ranking score — not a probability
    dimensions: AssessmentDimensions = Field(default_factory=AssessmentDimensions)
    strong_signals: list[str] = Field(default_factory=list)
    independent_sources: int = 0
    image_occurrence: ImageMatchType | None = None
    face_band: FaceMatchBand | None = None
    name_match: MatchState = MatchState.UNKNOWN
    location_match: MatchState = MatchState.UNKNOWN
    username_match: MatchState = MatchState.UNKNOWN
    email_match: MatchState = MatchState.UNKNOWN
    employer_match: MatchState = MatchState.UNKNOWN
    education_match: MatchState = MatchState.UNKNOWN
    reasons: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class CandidateIdentity(BaseModel):
    id: str
    display_name: str
    rank: int = 0
    page_ids: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    best_face: FaceEvidence | None = None
    cluster_reasons: list[str] = Field(default_factory=list)
    assessment: Assessment = Field(default_factory=Assessment)
