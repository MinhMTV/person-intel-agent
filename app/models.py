"""Data models for Person Intelligence Agent."""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Source(str, Enum):
    WEB = "web"
    SOCIAL = "social"
    LINKEDIN = "linkedin"
    XING = "xing"
    GITHUB = "github"
    EMAIL = "email"
    IMAGE = "image"
    ACADEMIC = "academic"
    BREACH = "breach"
    FORUM = "forum"
    VIDEO = "video"
    BUSINESS = "business"


class Confidence(str, Enum):
    HIGH = "high"       # Exact match, verified
    MEDIUM = "medium"   # Likely match, good indicators
    LOW = "low"         # Possible match, weak signals


class Location(BaseModel):
    raw: str
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    geo_expansion: list[str] = Field(default_factory=list)  # Expanded search terms


class SocialProfile(BaseModel):
    platform: str
    url: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    bio: Optional[str] = None
    followers: Optional[int] = None
    verified: bool = False
    confidence: Confidence = Confidence.MEDIUM
    found_at: datetime = Field(default_factory=datetime.utcnow)


class SearchResult(BaseModel):
    source: Source
    title: str
    url: str
    snippet: Optional[str] = None
    confidence: Confidence = Confidence.MEDIUM
    found_at: datetime = Field(default_factory=datetime.utcnow)


class ImageMatch(BaseModel):
    source_url: str
    image_url: str
    similarity_score: float = Field(ge=0.0, le=1.0)
    context: Optional[str] = None  # Where the image was found
    found_at: datetime = Field(default_factory=datetime.utcnow)


class PersonQuery(BaseModel):
    """Input query for person search."""
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nicknames: list[str] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    include_countries: list[str] = Field(default_factory=list)
    exclude_countries: list[str] = Field(default_factory=list)
    include_continents: list[str] = Field(default_factory=list)
    exclude_continents: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    photo_path: Optional[str] = None
    age_range: Optional[tuple[int, int]] = None
    language: Optional[str] = None


class PersonDossier(BaseModel):
    """Complete intelligence dossier."""
    query: PersonQuery
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    # Results by category
    social_profiles: list[SocialProfile] = Field(default_factory=list)
    web_results: list[SearchResult] = Field(default_factory=list)
    email_addresses: list[str] = Field(default_factory=list)
    image_matches: list[ImageMatch] = Field(default_factory=list)
    professional: list[SearchResult] = Field(default_factory=list)
    academic: list[SearchResult] = Field(default_factory=list)
    developer: list[SearchResult] = Field(default_factory=list)

    # Metadata
    scanners_used: list[str] = Field(default_factory=list)
    total_sources_checked: int = 0
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"# Person Intelligence Dossier",
            f"**Name:** {self.query.full_name}",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Confidence:** {self.confidence_score:.0%}",
            "",
            "## Social Profiles",
        ]
        for p in self.social_profiles:
            lines.append(f"- **{p.platform}**: [{p.username or p.url}]({p.url}) ({p.confidence.value})")
        lines.append("")
        lines.append("## Emails Found")
        for e in self.email_addresses:
            lines.append(f"- {e}")
        lines.append("")
        lines.append("## Web Mentions")
        for r in self.web_results[:10]:
            lines.append(f"- [{r.title}]({r.url})")
        return "\n".join(lines)
