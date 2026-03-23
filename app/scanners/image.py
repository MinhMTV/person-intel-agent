"""Image Similarity Scanner — Face recognition + reverse image search + social profile scraping.

Uses DeepFace/ArcFace as primary backend with dlib fallback.
Includes face quality scoring and demographic estimation.
"""

from __future__ import annotations
import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models import PersonQuery, ImageMatch, Confidence


class ImageScanner:
    """Find images of a person using face recognition + reverse image search.

    Primary backend: DeepFace with ArcFace model (512-d embeddings, higher accuracy)
    Fallback: face_recognition/dlib (128-d embeddings)
    """

    name = "image"
    description = "Face recognition + reverse image search + social profile scraping"

    # Threshold: lower = stricter matching
    SIMILARITY_THRESHOLD = 0.6

    def __init__(self, backend: str = "deepface", model: str = "ArcFace"):
        self.backend = backend
        self.model = model
        self._engine = None

    @property
    def engine(self):
        """Lazy init face engine."""
        if self._engine is None:
            from app.scanners.face_engine import FaceEngine
            self._engine = FaceEngine(backend=self.backend, model=self.model)
        return self._engine

    async def scan(self, query: PersonQuery) -> list[ImageMatch]:
        """Run image similarity scan."""
        if not query.photo_path:
            return []

        if not os.path.exists(query.photo_path):
            print(f"⚠️ Photo not found: {query.photo_path}")
            return []

        # Step 1: Analyze reference photo quality + encode
        engine = self.engine
        analysis = engine.analyze(query.photo_path)

        if not analysis.face_detected:
            print("⚠️ No face found in reference photo")
            return []

        if analysis.quality:
            print(f"📸 Reference face quality: {analysis.quality.quality_grade} ({analysis.quality.quality_score}/100)")
        if analysis.age_estimate:
            print(f"👤 Estimated age: {analysis.age_estimate}, gender: {analysis.gender}")
        print(f"🧠 Using {analysis.backend}/{analysis.model} ({len(analysis.embedding)}d embedding)")

        # Step 2: Find candidate images from multiple sources
        candidate_images = await self._find_candidate_images(query)
        print(f"🔍 Found {len(candidate_images)} candidate images to check")

        # Step 3: Download and compare each image
        matches = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for img_url, source_url, context in candidate_images:
                try:
                    match = await self._check_image(
                        client, img_url, source_url, context, query.photo_path
                    )
                    if match:
                        matches.append(match)
                        print(f"  ✅ Match! Similarity: {match.similarity_score:.0%} — {source_url[:50]}")
                except Exception:
                    pass

        # Sort by similarity (highest = best match)
        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches

    async def _find_candidate_images(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Find candidate images from multiple sources.
        Returns list of (image_url, source_page_url, context).
        """
        candidates = []

        # Source 1: Google Images via Playwright
        candidates.extend(await self._search_google_images(query))

        # Source 2: GitHub avatars (API-based, fast)
        candidates.extend(await self._fetch_github_avatars(query))

        # Source 3: Social profile scraping
        candidates.extend(await self._fetch_social_profiles(query))

        return candidates

    # =========================================================================
    # SOURCE 1: Google Images via Playwright
    # =========================================================================
    async def _search_google_images(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Search Google Images via Playwright for face photos."""
        candidates = []

        search_terms = [
            f'"{query.full_name}" photo portrait',
            f'"{query.full_name}" headshot',
        ]
        if query.locations:
            search_terms.append(f'"{query.full_name}" {query.locations[0].raw} photo')

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                for term in search_terms[:3]:
                    try:
                        import urllib.parse
                        encoded = urllib.parse.quote_plus(term)
                        url = f"https://www.google.com/search?q={encoded}&tbm=isch&tbs=itp:face"

                        await page.goto(url, wait_until="networkidle", timeout=15000)
                        await asyncio.sleep(2)

                        # Click on a few thumbnails to load full-res images
                        thumbnails = await page.query_selector_all("img.YQ4gaf")
                        for thumb in thumbnails[:8]:
                            try:
                                await thumb.click()
                                await asyncio.sleep(1)
                            except Exception:
                                pass

                        # Extract all image URLs
                        images = await page.query_selector_all("img")
                        for img in images:
                            src = await img.get_attribute("src") or ""
                            if (src.startswith("http") 
                                and not src.endswith((".svg", ".ico", ".gif"))
                                and "google" not in src
                                and "gstatic" not in src
                                and len(src) > 50):
                                candidates.append((src, url, f"Google Images: {term}"))

                        await asyncio.sleep(2)
                    except Exception as e:
                        print(f"  Google Images error: {e}")

                await browser.close()
        except Exception as e:
            print(f"  Playwright error: {e}")

        return candidates

    # =========================================================================
    # SOURCE 2: GitHub Avatars (direct API)
    # =========================================================================
    async def _fetch_github_avatars(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Fetch GitHub avatar URLs for matching usernames."""
        candidates = []
        usernames = self._generate_search_usernames(query)

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for username in usernames:
                try:
                    # GitHub API is public, no auth needed for user lookup
                    resp = await client.get(
                        f"https://api.github.com/users/{username}",
                        headers={"Accept": "application/vnd.github.v3+json"}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        avatar = data.get("avatar_url", "")
                        html_url = data.get("html_url", "")
                        if avatar:
                            # GitHub avatars are 400px by default, request larger
                            avatar_hd = f"{avatar}&s=400"
                            candidates.append((avatar_hd, html_url, f"GitHub: {username}"))
                except Exception:
                    pass
                await asyncio.sleep(0.5)  # Rate limit

        return candidates

    # =========================================================================
    # SOURCE 3: Social Profile Scraping
    # =========================================================================
    async def _fetch_social_profiles(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Scrape profile pictures from social media."""
        candidates = []

        # GitHub (already covered above, but also try search)
        candidates.extend(await self._scrape_github_search(query))

        # Instagram (via instaloader)
        candidates.extend(await self._scrape_instagram(query))

        return candidates

    async def _scrape_github_search(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Search GitHub for users by name and collect avatars."""
        candidates = []

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            try:
                import urllib.parse
                name = urllib.parse.quote_plus(query.full_name)
                resp = await client.get(
                    f"https://api.github.com/search/users?q={name}+type:user&per_page=5",
                    headers={"Accept": "application/vnd.github.v3+json"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for user in data.get("items", []):
                        avatar = user.get("avatar_url", "")
                        html_url = user.get("html_url", "")
                        if avatar:
                            candidates.append((f"{avatar}&s=400", html_url, f"GitHub user: {user.get('login')}"))
            except Exception:
                pass

        return candidates

    async def _scrape_instagram(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Download Instagram profile pictures via instaloader (public profiles)."""
        candidates = []
        usernames = self._generate_search_usernames(query)

        try:
            import instaloader

            loader = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
            )

            for username in usernames[:5]:
                try:
                    profile = instaloader.Profile.from_username(loader.context, username)
                    pic_url = profile.profile_pic_url
                    if pic_url:
                        candidates.append((
                            pic_url,
                            f"https://instagram.com/{username}",
                            f"Instagram: {username} ({profile.full_name})"
                        ))
                except Exception:
                    pass
                await asyncio.sleep(1)
        except Exception as e:
            print(f"  Instagram scraping error: {e}")

        return candidates

    # =========================================================================
    # UTILITIES
    # =========================================================================
    def _generate_search_usernames(self, query: PersonQuery) -> list[str]:
        """Generate likely usernames from name."""
        usernames = list(query.usernames)

        if query.first_name and query.last_name:
            fn = query.first_name.lower()
            ln = query.last_name.lower()
            usernames.extend([
                f"{fn}{ln}",
                f"{fn}.{ln}",
                f"{fn}_{ln}",
                f"{fn[0]}{ln}",
                f"{ln}{fn}",
                f"{ln}.{fn}",
                f"{fn}-{ln}",
            ])

        for nick in query.nicknames:
            nick = nick.lower()
            usernames.append(nick)
            if query.last_name:
                ln = query.last_name.lower()
                usernames.append(f"{nick}{ln}")

        return list(set(usernames))

    async def _check_image(
        self,
        client: httpx.AsyncClient,
        img_url: str,
        source_url: str,
        context: str,
        reference_path: str,
    ) -> Optional[ImageMatch]:
        """Download image, detect faces, compare with reference using face engine."""
        # Download image
        resp = await client.get(img_url)
        if resp.status_code != 200:
            return None

        # Check content type
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and len(resp.content) < 1000:
            return None

        # Save temp file
        suffix = ".png" if "png" in content_type else ".jpg"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
            # Use face engine for comparison
            result = self.engine.compare(reference_path, tmp_path, self.SIMILARITY_THRESHOLD)

            if result.get("is_match"):
                return ImageMatch(
                    source_url=source_url,
                    image_url=img_url,
                    similarity_score=result.get("similarity", 0),
                    context=f"{context} [{result.get('backend', '')}]",
                )

            return None
        finally:
            os.unlink(tmp_path)
