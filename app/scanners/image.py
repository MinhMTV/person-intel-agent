"""Image Similarity Scanner — Face recognition + reverse image search."""

from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models import PersonQuery, ImageMatch, Confidence


class ImageScanner:
    """Find images of a person using face recognition + reverse image search."""

    name = "image"
    description = "Face recognition + reverse image search"

    # Threshold: lower = stricter matching
    # 0.0 = identical, 0.6 = very similar, 1.0 = completely different
    SIMILARITY_THRESHOLD = 0.6

    def __init__(self):
        self._fr = None

    @property
    def face_recognition(self):
        """Lazy import face_recognition."""
        if self._fr is None:
            import face_recognition as fr
            self._fr = fr
        return self._fr

    async def scan(self, query: PersonQuery) -> list[ImageMatch]:
        """Run image similarity scan."""
        if not query.photo_path:
            return []

        if not os.path.exists(query.photo_path):
            print(f"⚠️ Photo not found: {query.photo_path}")
            return []

        # Step 1: Encode reference photo
        reference_encoding = self._encode_face(query.photo_path)
        if reference_encoding is None:
            print("⚠️ No face found in reference photo")
            return []

        print(f"📸 Reference face encoded from: {query.photo_path}")

        # Step 2: Find candidate images from web
        candidate_images = await self._find_candidate_images(query)
        print(f"🔍 Found {len(candidate_images)} candidate images to check")

        # Step 3: Download and compare each image
        matches = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for img_url, source_url, context in candidate_images:
                try:
                    match = await self._check_image(
                        client, img_url, source_url, context, reference_encoding
                    )
                    if match:
                        matches.append(match)
                        print(f"  ✅ Match! Distance: {match.similarity_score:.2f} — {source_url[:50]}")
                except Exception as e:
                    pass  # Skip failed downloads

        # Sort by similarity (lowest distance = best match)
        matches.sort(key=lambda m: m.similarity_score)
        return matches

    def _encode_face(self, image_path: str) -> Optional:
        """Encode a face from an image file into a 128-d vector."""
        fr = self.face_recognition
        try:
            image = fr.load_image_file(image_path)
            encodings = fr.face_encodings(image)
            if encodings:
                return encodings[0]
            return None
        except Exception as e:
            print(f"Error encoding face: {e}")
            return None

    async def _find_candidate_images(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Find candidate images from various sources.
        Returns list of (image_url, source_page_url, context).
        """
        candidates = []

        # Source 1: Google Image Search (via DuckDuckGo)
        candidates.extend(await self._search_images_ddg(query))

        # Source 2: LinkedIn profile pictures (if we have URLs)
        # Source 3: Known social media profile pictures
        for profile_url in query.usernames:
            if "linkedin.com" in profile_url or "xing.com" in profile_url:
                candidates.append((profile_url, profile_url, "Profile picture"))

        return candidates

    async def _search_images_ddg(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Search for images via DuckDuckGo."""
        candidates = []
        search_terms = [
            f'"{query.full_name}" photo',
            f'"{query.full_name}" portrait',
            f'"{query.full_name}" {query.locations[0].raw}' if query.locations else None,
        ]
        search_terms = [s for s in search_terms if s]

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for term in search_terms[:3]:
                try:
                    import urllib.parse
                    encoded = urllib.parse.quote_plus(term)

                    # DuckDuckGo image search
                    url = f"https://duckduckgo.com/?q={encoded}&iax=images&ia=images"
                    resp = await client.get(url, headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    })

                    # Extract image URLs from the page
                    soup = BeautifulSoup(resp.text, "html.parser")

                    # DuckDuckGo loads images via JS, try to find image data
                    for img in soup.select("img[src]"):
                        src = img.get("src", "")
                        if src.startswith("http") and not src.endswith((".svg", ".ico")):
                            if "duckduckgo.com" not in src:
                                candidates.append((src, url, f"DDG: {term}"))

                    await asyncio.sleep(1)
                except Exception:
                    pass

        return candidates

    async def _check_image(
        self,
        client: httpx.AsyncClient,
        img_url: str,
        source_url: str,
        context: str,
        reference_encoding,
    ) -> Optional[ImageMatch]:
        """Download image, detect faces, compare with reference."""
        fr = self.face_recognition

        # Download image
        resp = await client.get(img_url)
        if resp.status_code != 200:
            return None

        # Check content type
        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and len(resp.content) < 1000:
            return None

        # Save temp file
        import tempfile
        suffix = ".jpg"
        if "png" in content_type:
            suffix = ".png"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
            # Load and encode faces
            image = fr.load_image_file(tmp_path)
            face_encodings = fr.face_encodings(image)

            if not face_encodings:
                return None

            # Compare each face in the image with reference
            best_distance = 1.0
            for encoding in face_encodings:
                distance = fr.face_distance([reference_encoding], encoding)[0]
                best_distance = min(best_distance, distance)

            if best_distance <= self.SIMILARITY_THRESHOLD:
                return ImageMatch(
                    source_url=source_url,
                    image_url=img_url,
                    similarity_score=round(1.0 - best_distance, 3),  # Convert to similarity (higher = better)
                    context=context,
                )

            return None
        finally:
            # Cleanup temp file
            os.unlink(tmp_path)
