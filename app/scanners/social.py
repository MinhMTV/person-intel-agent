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
        usernames = self._generate_usernames(query)[:16]

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
        parts = [part.lower() for part in query.full_name.split() if part.strip()]

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
        if len(parts) >= 3:
            first = parts[0]
            middle_parts = parts[1:-1]
            last = parts[-1]
            middle = "".join(middle_parts)
            initials = "".join(part[0] for part in parts)
            tail_initials = "".join(part[0] for part in parts[1:])
            middle_last = "".join(parts[1:])
            usernames.extend([
                f"{first}{middle}{last}",
                f"{first}{middle_parts[0]}{last}" if middle_parts else "",
                f"{first}{tail_initials}",
                f"{first}{initials}",
                f"{first}.{middle_parts[0]}.{last}" if middle_parts else "",
                f"{first}{middle_last}",
                f"{first}{middle}",
                f"{middle}{last}",
                f"{first}{middle_parts[0]}{last[0]}" if middle_parts and last else "",
                f"{first}{tail_initials}{last[0]}" if tail_initials and last else "",
                f"{first}{tail_initials[0]}" if tail_initials else "",
                f"{first}{tail_initials}",
                f"{first}{middle_parts[0]}{last[0]}" if middle_parts else "",
                f"{first}.{last}.{initials}",
                f"{first}.{last[:2]}.{initials}" if len(last) >= 2 else "",
                f"{first}.{last[:2]}.{tail_initials}" if len(last) >= 2 else "",
                f"{first}.{last[:2]}.{initials[-1]}" if len(last) >= 2 and initials else "",
                f"{first}.{last[:2]}.{tail_initials[0]}{last[0]}" if len(last) >= 2 and tail_initials and last else "",
                f"{first}{last[:2]}{initials}",
                f"{first}{last[:2]}{tail_initials}",
                f"{first}{last[:2]}",
                f"{first}{middle_parts[0][0]}{last[0]}" if middle_parts else "",
            ])

        # Add nicknames
        for nick in query.nicknames:
            nick = nick.lower()
            usernames.append(nick)
            if query.last_name:
                ln = query.last_name.lower()
                usernames.append(f"{nick}{ln}")
                usernames.append(f"{nick}.{ln}")

        cleaned = []
        seen = set()
        for username in usernames:
            username = str(username).strip().strip(".-_")
            if not username:
                continue
            if username not in seen:
                seen.add(username)
                cleaned.append(username)
        return cleaned

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
                        # Try to extract profile image
                        image_url = await self._extract_profile_image(client, platform, resp.text, final_url)
                        
                        return SocialProfile(
                            platform=platform,
                            url=final_url,
                            username=username,
                            confidence=Confidence.MEDIUM,
                            image_url=image_url,
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

    async def _extract_profile_image(self, client, platform: str, html: str, url: str) -> str | None:
        """Extract profile image URL from platform HTML."""
        from bs4 import BeautifulSoup
        
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Platform-specific image selectors
            if platform == "github":
                # GitHub avatar
                img = soup.select_one("img.avatar-user, img[src*='avatars.githubusercontent.com']")
                if img:
                    return img.get("src")
            
            elif platform == "twitter":
                # Twitter/X profile image
                img = soup.select_one("img[src*='pbs.twimg.com/profile_images']")
                if img:
                    return img.get("src")
            
            elif platform == "instagram":
                # Instagram profile image (meta tag)
                meta = soup.select_one("meta[property='og:image']")
                if meta:
                    return meta.get("content")
            
            elif platform == "linkedin":
                # LinkedIn profile image (meta tag)
                meta = soup.select_one("meta[property='og:image']")
                if meta:
                    return meta.get("content")
            
            elif platform == "facebook":
                # Facebook profile image
                meta = soup.select_one("meta[property='og:image']")
                if meta:
                    return meta.get("content")
            
            elif platform == "reddit":
                # Reddit avatar
                img = soup.select_one("img[src*='i.redd.it']")
                if img:
                    return img.get("src")
            
            elif platform == "xing":
                # Xing profile image
                meta = soup.select_one("meta[property='og:image']")
                if meta:
                    return meta.get("content")
            
            # Fallback: og:image meta tag
            meta = soup.select_one("meta[property='og:image']")
            if meta:
                return meta.get("content")
            
            # Fallback: first large image
            for img in soup.select("img[src]"):
                src = img.get("src", "")
                if any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    if any(size in src.lower() for size in ["avatar", "profile", "photo", "picture"]):
                        return src
            
        except Exception:
            pass
        
        return None
