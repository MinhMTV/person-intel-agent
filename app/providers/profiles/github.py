"""GitHub REST API: profiles for supplied usernames and name search."""

from __future__ import annotations

import asyncio

import httpx

from app.config import Settings
from app.domain.identity import IdentityHints
from app.providers.base import ProviderRateLimited, check_response, json_or_raise
from app.providers.profiles.base import ProfileProvider, ProfileRecord

API = "https://api.github.com"


class GitHubProfileProvider(ProfileProvider):
    name = "github"

    def __init__(self, settings: Settings, client: httpx.AsyncClient, max_name_results: int = 4):
        self.settings = settings
        self.client = client
        self.max_name_results = max_name_results

    def is_configured(self) -> bool:
        return self.settings.github_enabled

    def applicable(self, hints: IdentityHints) -> bool:
        return bool(hints.name or hints.usernames)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token.get_secret_value()}"
        return headers

    async def _user(self, login: str, lookup: str) -> ProfileRecord | None:
        resp = await self.client.get(f"{API}/users/{login}", headers=self._headers())
        if resp.status_code == 404:
            return None
        if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
            raise ProviderRateLimited("github: rate limited")
        check_response(resp, self.name)
        data = json_or_raise(resp, self.name)
        if not isinstance(data, dict) or data.get("type") not in (None, "User"):
            return None
        blog = (data.get("blog") or "").strip()
        if blog and not blog.startswith(("http://", "https://")):
            blog = "https://" + blog
        twitter = data.get("twitter_username")
        return ProfileRecord(
            provider=self.name,
            platform="github",
            url=data.get("html_url") or f"https://github.com/{login}",
            username=data.get("login") or login,
            display_name=data.get("name"),
            avatar_url=data.get("avatar_url"),
            bio=(data.get("bio") or None),
            location=data.get("location"),
            organization=(data.get("company") or "").lstrip("@") or None,
            website=blog or None,
            emails=[data["email"]] if data.get("email") else [],
            linked_urls=[u for u in [blog, f"https://x.com/{twitter}" if twitter else ""] if u],
            lookup=lookup,
        )

    async def lookup(self, hints: IdentityHints) -> list[ProfileRecord]:
        logins: list[tuple[str, str]] = [(u, f"username:{u}") for u in hints.usernames[:5]]
        if hints.name:
            q = f'"{hints.name}" in:name type:user'
            if hints.location:
                q += f' location:"{hints.location}"'
            resp = await self.client.get(
                f"{API}/search/users", params={"q": q, "per_page": self.max_name_results}, headers=self._headers()
            )
            if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
                raise ProviderRateLimited("github: rate limited")
            check_response(resp, self.name)
            data = json_or_raise(resp, self.name)
            items = data.get("items", []) if isinstance(data, dict) else []
            logins += [
                (i["login"], f"name:{hints.name}")
                for i in items[: self.max_name_results]
                if isinstance(i, dict) and i.get("login")
            ]
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for login, lookup in logins:
            if login.lower() not in seen:
                seen.add(login.lower())
                unique.append((login, lookup))
        records = await asyncio.gather(*(self._user(login, lookup) for login, lookup in unique))
        return [r for r in records if r]
