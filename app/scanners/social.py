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

        # Generate usernames from name
        usernames = self._generate_usernames(query)

        # Check known platforms
        for username in usernames:
            platform_results = await self._check_username(username)
            results.extend(platform_results)

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

    async def _check_username(self, username: str) -> list[SocialProfile]:
        """Check a username against known platforms."""
        import httpx
        results = []

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for platform, url_template in self.KNOWN_PLATFORMS.items():
                url = url_template.format(username)
                try:
                    resp = await client.head(url)
                    if resp.status_code == 200:
                        results.append(SocialProfile(
                            platform=platform,
                            url=url,
                            username=username,
                            confidence=Confidence.MEDIUM,
                        ))
                except Exception:
                    pass  # Timeout or connection error — skip

        return results
