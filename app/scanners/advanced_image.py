"""Advanced Image Scanner — Google Lens + Bing Visual Search via Playwright.

Uses DeepFace/ArcFace for face comparison with dlib fallback.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

from app.models import PersonQuery, ImageMatch, Confidence


class AdvancedImageScanner:
    """Reverse image search via Google Lens and Bing Visual Search.

    Uses Playwright to automate browser-based reverse image search engines,
    then downloads candidate images and compares them with DeepFace/ArcFace.
    """

    name = "advanced_image"
    description = "Google Lens + Bing Visual Search reverse image analysis"

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
        """Run advanced image search."""
        if not query.photo_path or not os.path.exists(query.photo_path):
            return []

        # Analyze reference
        engine = self.engine
        analysis = engine.analyze(query.photo_path)
        if not analysis.face_detected:
            print("⚠️ No face found in reference photo")
            return []

        print(f"📸 [AdvancedImage] Using {analysis.backend}/{analysis.model} ({len(analysis.embedding) if analysis.embedding is not None else 0}d embedding)")

        # Collect candidates from reverse search engines
        candidates: list[tuple[str, str, str]] = []

        candidates.extend(await self._google_lens_search(query))
        candidates.extend(await self._bing_visual_search(query))

        print(f"🔍 [AdvancedImage] Found {len(candidates)} candidate images from reverse search")

        # Download and compare each image
        matches: list[ImageMatch] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for img_url, source_url, context in candidates:
                try:
                    match = await self._check_image(
                        client, img_url, source_url, context, query.photo_path
                    )
                    if match:
                        matches.append(match)
                        print(f"  ✅ Advanced match! {match.similarity_score:.0%} — {source_url[:50]}")
                except Exception:
                    pass

        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches

    # ==================================================================
    # Google Lens reverse search
    # ==================================================================

    async def _google_lens_search(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Perform reverse image search via Google Lens.

        Uploads the reference photo to Google Lens and extracts visually
        similar images and pages containing the image.
        """
        candidates: list[tuple[str, str, str]] = []

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                try:
                    # Navigate to Google Lens
                    await page.goto("https://lens.google.com/", wait_until="networkidle", timeout=15000)
                    await asyncio.sleep(1)

                    # Look for the file upload input
                    file_input = await page.query_selector("input[type='file']")
                    if file_input:
                        await file_input.set_input_files(query.photo_path)
                        await asyncio.sleep(5)  # Wait for processing
                        await page.wait_for_load_state("networkidle", timeout=10000)

                        # Extract similar image URLs
                        images = await page.query_selector_all("img")
                        for img in images:
                            src = await img.get_attribute("src") or ""
                            if (src.startswith("http")
                                and not src.endswith((".svg", ".ico", ".gif"))
                                and "google" not in src
                                and "gstatic" not in src
                                and len(src) > 50):
                                candidates.append((src, page.url, "Google Lens: visual match"))

                        # Also try "Find image source" tab
                        source_link = await page.query_selector("a[href*='searchbyimage']")
                        if source_link:
                            href = await source_link.get_attribute("href") or ""
                            if href:
                                await page.goto(href, wait_until="networkidle", timeout=10000)
                                await asyncio.sleep(2)

                                images = await page.query_selector_all("img")
                                for img in images:
                                    src = await img.get_attribute("src") or ""
                                    if (src.startswith("http")
                                        and "google" not in src
                                        and "gstatic" not in src
                                        and len(src) > 50):
                                        candidates.append((src, page.url, "Google Lens: source page"))

                except Exception as e:
                    print(f"  Google Lens error: {e}")
                finally:
                    await browser.close()

        except Exception as e:
            print(f"  Playwright (Google Lens) unavailable: {e}")

        return candidates

    # ==================================================================
    # Bing Visual Search
    # ==================================================================

    async def _bing_visual_search(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Perform reverse image search via Bing Visual Search.

        Uploads the reference photo to Bing image search and extracts
        visually similar images.
        """
        candidates: list[tuple[str, str, str]] = []

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                try:
                    # Navigate to Bing Images
                    await page.goto("https://www.bing.com/images", wait_until="networkidle", timeout=15000)
                    await asyncio.sleep(1)

                    # Click the camera/search-by-image icon
                    camera_btn = await page.query_selector("#sbi_b, a#b-scopeListItem-visual_search")
                    if camera_btn:
                        await camera_btn.click()
                        await asyncio.sleep(1)

                    # Upload file
                    file_input = await page.query_selector("input[type='file']")
                    if file_input:
                        await file_input.set_input_files(query.photo_path)
                        await asyncio.sleep(5)
                        await page.wait_for_load_state("networkidle", timeout=15000)

                        # Extract images from results
                        images = await page.query_selector_all("img.mimg, img.img_cont, a.iusc img")
                        for img in images:
                            src = await img.get_attribute("src") or ""
                            data_src = await img.get_attribute("data-src") or ""
                            url = src or data_src

                            if (url.startswith("http")
                                and "bing" not in url
                                and "msn" not in url
                                and len(url) > 50):
                                candidates.append((url, page.url, "Bing Visual Search: similar image"))

                        # Also extract from "Pages with this image" section
                        page_links = await page.query_selector_all("a.iusc")
                        for link in page_links[:10]:
                            m = await link.get_attribute("m")
                            if m:
                                import json as _json
                                try:
                                    meta = _json.loads(m)
                                    purl = meta.get("purl", "")
                                    murl = meta.get("murl", "")
                                    if murl and murl.startswith("http"):
                                        candidates.append((murl, purl or page.url, "Bing Visual Search: source page"))
                                except Exception:
                                    pass

                except Exception as e:
                    print(f"  Bing Visual Search error: {e}")
                finally:
                    await browser.close()

        except Exception as e:
            print(f"  Playwright (Bing) unavailable: {e}")

        return candidates

    # ==================================================================
    # Utilities
    # ==================================================================

    async def _check_image(
        self,
        client: httpx.AsyncClient,
        img_url: str,
        source_url: str,
        context: str,
        reference_path: str,
    ) -> Optional[ImageMatch]:
        """Download image, detect faces, compare with reference using face engine."""

        resp = await client.get(img_url)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and len(resp.content) < 1000:
            return None

        suffix = ".png" if "png" in content_type else ".jpg"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
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
