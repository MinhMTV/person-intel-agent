"""Gravatar: public avatar/profile attached to a *supplied* email address."""

from __future__ import annotations

import asyncio
import hashlib

import httpx

from app.config import Settings
from app.domain.identity import IdentityHints
from app.providers.base import check_response, json_or_raise
from app.providers.profiles.base import ProfileProvider, ProfileRecord


class GravatarProfileProvider(ProfileProvider):
    name = "gravatar"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def is_configured(self) -> bool:
        return True

    def applicable(self, hints: IdentityHints) -> bool:
        return bool(hints.emails)

    async def _one(self, email: str) -> ProfileRecord | None:
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        resp = await self.client.get(f"https://api.gravatar.com/v3/profiles/{digest}")
        if resp.status_code == 404:
            return None
        check_response(resp, self.name)
        data = json_or_raise(resp, self.name)
        if not isinstance(data, dict):
            return None
        accounts = [a.get("url") for a in data.get("verified_accounts") or [] if isinstance(a, dict) and a.get("url")]
        return ProfileRecord(
            provider=self.name,
            platform="gravatar",
            url=data.get("profile_url") or f"https://gravatar.com/{digest}",
            display_name=data.get("display_name"),
            avatar_url=data.get("avatar_url") or f"https://gravatar.com/avatar/{digest}?s=400&d=404",
            bio=data.get("description") or None,
            location=data.get("location") or None,
            organization=data.get("company") or None,
            emails=[email],
            linked_urls=accounts,
            lookup=f"email:{email}",
        )

    async def lookup(self, hints: IdentityHints) -> list[ProfileRecord]:
        records = await asyncio.gather(*(self._one(e) for e in hints.emails[:3]))
        return [r for r in records if r]
