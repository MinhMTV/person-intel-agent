"""Social Media Scanner — wraps Sherlock/Maigret for username discovery."""

from __future__ import annotations
import asyncio
from app.models import PersonQuery, SocialProfile, Confidence
from app.scanners.base import BaseScanner


class SocialScanner(BaseScanner):
    """Search social media platforms via Sherlock/Maigret."""

    name = "social"
    description = "Username search across 300+ social networks"

    # Platform URLs to check directly
    KNOWN_PLATFORMS = {
        "twitter": "https://twitter.com/{}",
        "instagram": "https://instagram.com/{}",
        "linkedin": "https://linkedin.com/in/{}",
        "github": "https://github.com/{}",
        "reddit": "https://reddit.com/user/{}",
        "facebook": "https://facebook.com/{}",
        "tiktok": "https://tiktok.com/@{}",
        "youtube": "https://youtube.com/@{}",
        "xing": "https://xing.com/profile/{}",
    }

    async def scan(self, query: PersonQuery) -> list[SocialProfile]:
        """Run social media scan."""
        results = []
        selected_platforms = set(p.lower() for p in query.include_platforms or [])

        # Generate usernames from name
        usernames = self._generate_usernames(query)[:6]

        # Check known platforms
        checks = [self._check_username(username, selected_platforms) for username in usernames]
        completed = await asyncio.gather(*checks, return_exceptions=True)
        for item in completed:
            if isinstance(item, Exception):
                continue
            results.extend(item)

        # Deduplicate
        seen = set()
        unique = []
        for r in results:
            key = (r.platform, r.url)
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return unique

    def _generate_usernames(self, query: PersonQuery) -> list[str]:
        """Generate likely usernames from name."""
        usernames = list(query.usernames)  # Start with known usernames

        if query.first_name and query.last_name:
            fn = query.first_name.lower()
            ln = query.last_name.lower()
            usernames.extend([
                f"{fn}{ln}",           # johnsmith
                f"{fn}.{ln}",          # john.smith
                f"{fn}_{ln}",          # john_smith
                f"{fn[0]}{ln}",        # jsmith
                f"{fn}{ln[0]}",        # johns
                f"{ln}{fn}",           # smithjohn
                f"{ln}.{fn}",          # smith.john
                f"{fn}-{ln}",          # john-smith
            ])

        # Add nicknames
        for nick in query.nicknames:
            nick = nick.lower()
            usernames.append(nick)
            if query.last_name:
                ln = query.last_name.lower()
                usernames.append(f"{nick}{ln}")
                usernames.append(f"{nick}.{ln}")

        return list(set(usernames))

    async def _check_username(self, username: str, selected_platforms: set[str] | None = None) -> list[SocialProfile]:
        """Check a username against known platforms."""
        import httpx
        results = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        selected_platforms = selected_platforms or set()
        platforms = [
            (platform, url_template)
            for platform, url_template in self.KNOWN_PLATFORMS.items()
            if not selected_platforms or platform.lower() in selected_platforms
        ]

        timeout = httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=3.0)
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers, limits=limits) as client:
            async def check_platform(platform: str, url_template: str):
                url = url_template.format(username)
                try:
                    resp = await client.get(url)
                    final_url = str(resp.url)
                    if resp.status_code == 200 and username.lower() in final_url.lower():
                        return SocialProfile(
                            platform=platform,
                            url=final_url,
                            username=username,
                            confidence=Confidence.MEDIUM,
                        )
                except Exception:
                    return None
                return None

            completed = await asyncio.gather(
                *(check_platform(platform, url_template) for platform, url_template in platforms),
                return_exceptions=True,
            )
            for item in completed:
                if isinstance(item, SocialProfile):
                    results.append(item)

        return results
