"""Data models for Person Intelligence Agent."""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


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
    image_url: Optional[str] = None
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
    image_url: Optional[str] = None
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
    include_platforms: list[str] = Field(default_factory=list)
    exclude_platforms: list[str] = Field(default_factory=list)
    include_countries: list[str] = Field(default_factory=list)
    exclude_countries: list[str] = Field(default_factory=list)
    include_continents: list[str] = Field(default_factory=list)
    exclude_continents: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    photo_path: Optional[str] = None
    age_range: Optional[tuple[int, int]] = None
    language: Optional[str] = None

    @model_validator(mode="after")
    def split_name(self):
        """Split full_name into first_name and last_name."""
        if not self.first_name or not self.last_name:
            parts = self.full_name.strip().split()
            if len(parts) >= 2:
                self.first_name = parts[0]
                self.last_name = parts[-1]
            elif len(parts) == 1:
                self.first_name = parts[0]
        return self


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
        """Generate a concise but complete dossier summary."""
        total_results = (
            len(self.social_profiles)
            + len(self.web_results)
            + len(self.image_matches)
            + len(self.email_addresses)
            + len(self.professional)
            + len(self.academic)
            + len(self.developer)
        )
        location_summary = ", ".join(loc.raw for loc in self.query.locations[:3] if loc.raw)
        social_top = sorted(self.social_profiles, key=_profile_sort_key, reverse=True)[:5]
        web_top = sorted(self.web_results, key=_search_result_sort_key, reverse=True)[:8]
        professional_top = sorted(self.professional, key=_search_result_sort_key, reverse=True)[:5]
        academic_top = sorted(self.academic, key=_search_result_sort_key, reverse=True)[:5]
        developer_top = sorted(self.developer, key=_search_result_sort_key, reverse=True)[:5]
        image_top = sorted(self.image_matches, key=lambda match: match.similarity_score, reverse=True)[:5]

        lines = [
            f"# Person Intelligence Dossier",
            f"**Name:** {self.query.full_name}",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Confidence:** {self.confidence_score:.0%}",
            f"**Findings:** {total_results} total",
            "",
        ]
        if location_summary:
            lines.append(f"**Locations in Scope:** {location_summary}")
            lines.append("")

        if total_results == 0:
            lines.append("No findings were collected for this query yet.")
            return "\n".join(lines)

        lines.extend([
            "## Result Breakdown",
            f"- Social Profiles: {len(self.social_profiles)}",
            f"- Professional: {len(self.professional)}",
            f"- Academic: {len(self.academic)}",
            f"- Developer: {len(self.developer)}",
            f"- Web Mentions: {len(self.web_results)}",
            f"- Image Matches: {len(self.image_matches)}",
            f"- Emails Found: {len(self.email_addresses)}",
            "",
        ])

        if social_top:
            lines.append("## Top Social Profiles")
            for p in social_top:
                details = []
                if p.username:
                    details.append(f"@{p.username}")
                if p.verified:
                    details.append("verified")
                details.append(p.confidence.value)
                lines.append(f"- **{p.platform}**: [{p.display_name or p.username or p.url}]({p.url}) ({', '.join(details)})")
            lines.append("")

        if professional_top:
            lines.append("## Professional Results")
            for r in professional_top:
                lines.append(f"- [{r.title}]({r.url}) ({r.confidence.value})")
            lines.append("")

        if academic_top:
            lines.append("## Academic Results")
            for r in academic_top:
                lines.append(f"- [{r.title}]({r.url}) ({r.confidence.value})")
            lines.append("")

        if developer_top:
            lines.append("## Developer Results")
            for r in developer_top:
                lines.append(f"- [{r.title}]({r.url}) ({r.confidence.value})")
            lines.append("")

        if self.email_addresses:
            lines.append("## Emails Found")
            for e in self.email_addresses[:10]:
                lines.append(f"- {e}")
            lines.append("")

        if web_top:
            lines.append("## Top Web Mentions")
            for r in web_top:
                snippet = f" — {r.snippet[:120]}" if r.snippet else ""
                lines.append(f"- [{r.title}]({r.url}) ({r.confidence.value}){snippet}")
            lines.append("")

        if image_top:
            lines.append("## Best Image Matches")
            for match in image_top:
                label = match.context or match.source_url
                lines.append(
                    f"- {match.similarity_score:.0%} match: [{label}]({match.source_url})"
                )
            lines.append("")

        if self.scanners_used:
            lines.append("## Scanners Used")
            lines.append(f"- {', '.join(self.scanners_used)}")

        return "\n".join(lines)


def _confidence_rank(value: Confidence | str | None) -> int:
    conf = value.value if hasattr(value, "value") else str(value or "").lower()
    return {"high": 3, "medium": 2, "low": 1}.get(conf, 0)


def _profile_sort_key(profile: SocialProfile) -> tuple[int, int, int, int]:
    return (
        _confidence_rank(profile.confidence),
        1 if profile.verified else 0,
        1 if profile.image_url else 0,
        profile.followers or 0,
    )


def _search_result_sort_key(result: SearchResult) -> tuple[int, int, int]:
    return (
        _confidence_rank(result.confidence),
        1 if result.image_url else 0,
        len(result.snippet or ""),
    )
