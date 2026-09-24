"""Investigation aggregate, results, provider runs, leads and progress events."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.candidate import CandidateIdentity, CandidatePage
from app.domain.identity import IdentityHints, InvestigationOptions
from app.domain.image import ImageDiscoveryResult, ReferenceImage, utcnow


class InvestigationStatus(str, Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"  # finished with timeouts/failures; results incomplete
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"  # process stopped while running

    @property
    def is_active(self) -> bool:
        return self in (InvestigationStatus.QUEUED, InvestigationStatus.RUNNING)


class ProviderOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    NO_RESULTS = "NO_RESULTS"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"


class ProviderRun(BaseModel):
    provider: str
    stage: str
    outcome: ProviderOutcome
    duration_ms: int = 0
    result_count: int = 0
    cache_hit: bool = False
    error: str | None = None
    detail: str | None = None  # e.g. the query that was run
    started_at: datetime = Field(default_factory=utcnow)


class LeadKind(str, Enum):
    EMAIL = "EMAIL"
    DOMAIN = "DOMAIN"


class LeadStatus(str, Enum):
    PROVIDED = "PROVIDED"  # supplied by the investigator
    OBSERVED = "OBSERVED"  # seen on a candidate page
    VERIFIED = "VERIFIED"  # verified by an independent check (e.g. SMTP / breach record)
    GENERATED_CANDIDATE = "GENERATED_CANDIDATE"  # hypothesis only


class Lead(BaseModel):
    """A hypothesis or identifier worth following up. Never identity evidence by itself."""

    kind: LeadKind
    value: str
    status: LeadStatus
    classification: str  # e.g. OBSERVED_EMAIL, GENERATED_EMAIL_CANDIDATE, DOMAIN_EXISTS_UNVERIFIED
    source_url: str | None = None
    note: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)


class WebEntity(BaseModel):
    description: str
    score: float | None = None
    entity_id: str | None = None
    provider: str = "google_vision"


class InvestigationStats(BaseModel):
    reverse_image_results: int = 0
    pages_discovered: int = 0
    pages_fetched: int = 0
    images_considered: int = 0
    images_downloaded: int = 0
    images_deduplicated: int = 0
    faces_detected: int = 0
    face_comparisons: int = 0
    candidate_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    duration_ms: int = 0


class InvestigationResult(BaseModel):
    investigation_id: str
    status: InvestigationStatus
    pipeline_version: str
    fingerprint: str
    started_at: datetime
    completed_at: datetime | None = None
    hints: IdentityHints
    reference_images: list[ReferenceImage] = Field(default_factory=list)
    reverse_image_results: list[ImageDiscoveryResult] = Field(default_factory=list)
    web_entities: list[WebEntity] = Field(default_factory=list)
    best_guess_labels: list[str] = Field(default_factory=list)
    pages: list[CandidatePage] = Field(default_factory=list)
    candidates: list[CandidateIdentity] = Field(default_factory=list)
    leads: list[Lead] = Field(default_factory=list)
    provider_runs: list[ProviderRun] = Field(default_factory=list)
    stats: InvestigationStats = Field(default_factory=InvestigationStats)
    warnings: list[str] = Field(default_factory=list)
    conclusion: str = ""
    face_matching_available: bool = False
    face_model: str | None = None


class Note(BaseModel):
    id: int
    text: str
    created_at: datetime = Field(default_factory=utcnow)


class Investigation(BaseModel):
    id: str
    status: InvestigationStatus = InvestigationStatus.DRAFT
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    hints: IdentityHints = Field(default_factory=IdentityHints)
    options: InvestigationOptions = Field(default_factory=InvestigationOptions)
    reference_images: list[ReferenceImage] = Field(default_factory=list)
    fingerprint: str | None = None
    result: InvestigationResult | None = None
    error: str | None = None
    pinned: bool = False
    tags: list[str] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)

    @property
    def title(self) -> str:
        if self.hints.name:
            return self.hints.name
        if self.result and self.result.candidates:
            return f"Image search → {self.result.candidates[0].display_name}"
        return "Image-only investigation"


class EventType(str, Enum):
    INVESTIGATION_STARTED = "INVESTIGATION_STARTED"
    IMAGE_VALIDATED = "IMAGE_VALIDATED"
    FACE_DETECTED = "FACE_DETECTED"
    REFERENCE_EMBEDDING_CREATED = "REFERENCE_EMBEDDING_CREATED"
    REVERSE_IMAGE_SEARCH_STARTED = "REVERSE_IMAGE_SEARCH_STARTED"
    REVERSE_IMAGE_RESULT_FOUND = "REVERSE_IMAGE_RESULT_FOUND"
    CANDIDATE_SEARCH_STARTED = "CANDIDATE_SEARCH_STARTED"
    PROVIDER_FINISHED = "PROVIDER_FINISHED"
    CANDIDATE_PAGE_DISCOVERED = "CANDIDATE_PAGE_DISCOVERED"
    CANDIDATE_IMAGE_DOWNLOADED = "CANDIDATE_IMAGE_DOWNLOADED"
    FACE_MATCH_FOUND = "FACE_MATCH_FOUND"
    TEXT_EVIDENCE_FOUND = "TEXT_EVIDENCE_FOUND"
    CANDIDATE_UPDATED = "CANDIDATE_UPDATED"
    WARNING = "WARNING"
    INVESTIGATION_COMPLETED = "INVESTIGATION_COMPLETED"
    INVESTIGATION_FAILED = "INVESTIGATION_FAILED"


TERMINAL_EVENTS = {EventType.INVESTIGATION_COMPLETED, EventType.INVESTIGATION_FAILED}


class ProgressEvent(BaseModel):
    seq: int = 0
    investigation_id: str
    type: EventType
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)
