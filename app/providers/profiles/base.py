"""Structured profile providers (APIs that return person/profile records)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.domain.identity import IdentityHints


class ProfileRecord(BaseModel):
    provider: str
    platform: str
    url: str
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    location: str | None = None
    organization: str | None = None
    website: str | None = None
    emails: list[str] = Field(default_factory=list)
    linked_urls: list[str] = Field(default_factory=list)
    lookup: str = ""  # which hint produced this record


class ProfileProvider(ABC):
    name: str = "profile"

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def applicable(self, hints: IdentityHints) -> bool: ...

    @abstractmethod
    async def lookup(self, hints: IdentityHints) -> list[ProfileRecord]: ...
