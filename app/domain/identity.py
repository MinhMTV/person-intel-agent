"""Optional identity hints supplied by the investigator."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils.canonical import (
    canonical_url,
    dedupe_preserve_order,
    normalize_email,
    normalize_name,
    normalize_text,
    normalize_username,
)

_MAX_LIST = 10
_MAX_TEXT = 200


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value)).strip()
    text = re.sub(r"\s+", " ", text)[:_MAX_TEXT]
    return text or None


def _split_list(value: object) -> list[str]:
    if value is None:
        return []
    items = re.split(r"[,\n;]", value) if isinstance(value, str) else [str(v) for v in value]  # type: ignore[attr-defined]
    return [c for c in (_clean_text(i) for i in items) if c][: _MAX_LIST * 2]


class IdentityHints(BaseModel):
    """All fields are optional. The system must work with none of them."""

    name: str | None = None
    location: str | None = None
    country: str | None = None
    age_min: int | None = Field(default=None, ge=0, le=120)
    age_max: int | None = Field(default=None, ge=0, le=120)
    usernames: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    employer: str | None = None
    university: str | None = None
    profession: str | None = None
    known_urls: list[str] = Field(default_factory=list)

    @field_validator("name", "location", "country", "employer", "university", "profession", mode="before")
    @classmethod
    def _text(cls, value: object) -> str | None:
        return _clean_text(value)

    @field_validator("usernames", mode="before")
    @classmethod
    def _usernames(cls, value: object) -> list[str]:
        cleaned = [normalize_username(v) for v in _split_list(value)]
        return dedupe_preserve_order([u for u in cleaned if len(u) >= 2])[:_MAX_LIST]

    @field_validator("emails", mode="before")
    @classmethod
    def _emails(cls, value: object) -> list[str]:
        raw = [v.strip().lower() for v in _split_list(value)]
        valid = [v for v in raw if normalize_email(v)]
        return dedupe_preserve_order(valid, key=normalize_email)[:_MAX_LIST]

    @field_validator("known_urls", mode="before")
    @classmethod
    def _urls(cls, value: object) -> list[str]:
        urls = []
        for item in _split_list(value):
            if not re.match(r"^https?://", item, re.IGNORECASE):
                item = "https://" + item
            if canonical_url(item).startswith("https://"):
                urls.append(item)
        return dedupe_preserve_order(urls, key=canonical_url)[:_MAX_LIST]

    @model_validator(mode="after")
    def _age_order(self) -> IdentityHints:
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            self.age_min, self.age_max = self.age_max, self.age_min
        return self

    def is_empty(self) -> bool:
        return not any(
            [
                self.name,
                self.location,
                self.country,
                self.usernames,
                self.emails,
                self.employer,
                self.university,
                self.profession,
                self.known_urls,
                self.age_min,
                self.age_max,
            ]
        )

    def normalized(self) -> dict[str, object]:
        """Canonical representation used for fingerprinting / cache keys."""
        return {
            "name": normalize_name(self.name),
            "location": normalize_text(self.location),
            "country": normalize_text(self.country),
            "age": [self.age_min, self.age_max],
            "usernames": sorted(normalize_username(u) for u in self.usernames),
            "emails": sorted(normalize_email(e) for e in self.emails),
            "employer": normalize_text(self.employer),
            "university": normalize_text(self.university),
            "profession": normalize_text(self.profession),
            "known_urls": sorted(canonical_url(u) for u in self.known_urls),
        }


class InvestigationOptions(BaseModel):
    use_reverse_image: bool = True
    use_web_search: bool = True
    use_profile_providers: bool = True
    max_candidate_pages: int | None = Field(default=None, ge=1, le=200)

    def normalized(self) -> dict[str, object]:
        return self.model_dump()
