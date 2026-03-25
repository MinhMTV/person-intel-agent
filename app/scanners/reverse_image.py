"""Reverse Image Search Scanner — Multi-engine reverse image search.

Search engines:
  - Yandex Reverse Image Search (best for faces)
  - TinEye (exact/similar image finder)
  - Wikidata/Wikimedia Commons (structured image database)
  - Google Lens (via existing advanced_image scanner)
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models import PersonQuery, ImageMatch, Confidence
from app.playwright_support import can_use_playwright, playwright_runtime_issue


class ReverseImageScanner:
    """Multi-engine reverse image search.

    Uses Yandex, TinEye, and Wikidata to find instances of a face image
    across the web. Each engine has different strengths:
      - Yandex: Best face search, finds Russian/CIS sources
      - TinEye: Finds exact image copies and modifications
      - Wikidata: Structured data, finds official/public photos
    """

    name = "reverse_image"
    description = "Yandex + TinEye + Wikidata reverse image search"

    SIMILARITY_THRESHOLD = 0.6

    def __init__(self, backend: str = "deepface", model: str = "ArcFace"):
        self.backend = backend
        self.model = model
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from app.scanners.face_engine import FaceEngine
            self._engine = FaceEngine(backend=self.backend, model=self.model)
        return self._engine

    async def scan(self, query: PersonQuery) -> list[ImageMatch]:
        """Run reverse image search across all engines."""
        if not query.photo_path or not os.path.exists(query.photo_path):
            return []
        if not can_use_playwright():
            print(playwright_runtime_issue())
            return []

        # Verify face exists in reference
        analysis = self.engine.analyze(query.photo_path)
        if not analysis.face_detected:
            print("⚠️ No face found in reference photo")
            return []

        print(f"📸 [ReverseImage] Searching with {analysis.backend}/{analysis.model}")

        # Collect candidates from all engines
        candidates: list[tuple[str, str, str]] = []

        candidates.extend(await self._yandex_reverse_search(query))
        candidates.extend(await self._tineye_search(query))
        candidates.extend(await self._wikidata_image_search(query))

        print(f"🔍 [ReverseImage] Found {len(candidates)} candidate images")

        # Download and compare
        matches: list[ImageMatch] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for img_url, source_url, context in candidates:
                try:
                    match = await self._check_image(
                        client, img_url, source_url, context, query.photo_path
                    )
                    if match:
                        matches.append(match)
                        print(f"  ✅ Reverse match! {match.similarity_score:.0%} — {source_url[:50]}")
                except Exception:
                    pass

        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches

    # =========================================================================
    # Yandex Reverse Image Search
    # =========================================================================

    async def _yandex_reverse_search(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Reverse image search via Yandex (best face search engine).

        Yandex has the best face recognition for reverse image search,
        especially for Eastern European/CIS sources.
        """
        candidates: list[tuple[str, str, str]] = []

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page = await context.new_page()

                try:
                    # Navigate to Yandex Images
                    await page.goto("https://yandex.com/images/", wait_until="networkidle", timeout=15000)
                    await asyncio.sleep(1)

                    # Click the camera/search-by-image icon
                    camera_btn = await page.query_selector("button.ImagesSearchForm-Button_type_reverse")
                    if not camera_btn:
                        camera_btn = await page.query_selector("[data-action='image-search']")

                    if camera_btn:
                        await camera_btn.click()
                        await asyncio.sleep(1)

                    # Upload file
                    file_input = await page.query_selector("input[type='file']")
                    if file_input:
                        await file_input.set_input_files(query.photo_path)
                        await asyncio.sleep(5)
                        await page.wait_for_load_state("networkidle", timeout=15000)

                        # Also try "Find face" filter (Yandex specialty)
                        face_filter = await page.query_selector("a[data-rbw='face']")
                        if face_filter:
                            try:
                                await face_filter.click()
                                await asyncio.sleep(3)
                            except Exception:
                                pass

                        # Extract images from results
                        images = await page.query_selector_all("img.serp-item__thumb, img.MMImage-Origin")
                        for img in images[:15]:
                            src = await img.get_attribute("src") or ""
                            if src.startswith("//"):
                                src = "https:" + src
                            if (src.startswith("http")
                                and "yandex" not in src
                                and "yimg" not in src
                                and len(src) > 30):
                                candidates.append((src, page.url, "Yandex: reverse image search"))

                        # Extract source pages
                        links = await page.query_selector_all("a.serp-item__link")
                        for link in links[:10]:
                            href = await link.get_attribute("href") or ""
                            if href and not href.startswith("https://yandex"):
                                # Try to get the thumbnail for this link
                                img = await link.query_selector("img")
                                if img:
                                    src = await img.get_attribute("src") or ""
                                    if src.startswith("//"):
                                        src = "https:" + src
                                    if src.startswith("http") and len(src) > 30:
                                        candidates.append((src, href, "Yandex: source page"))

                except Exception as e:
                    print(f"  Yandex reverse search error: {e}")
                finally:
                    await browser.close()

        except Exception as e:
            print(f"  Playwright (Yandex) unavailable: {e}")

        return candidates

    # =========================================================================
    # TinEye Reverse Image Search
    # =========================================================================

    async def _tineye_search(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Reverse image search via TinEye.

        TinEye specializes in finding exact and modified copies of images.
        Good for finding where a photo has been reposted.
        """
        candidates: list[tuple[str, str, str]] = []

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                try:
                    # Navigate to TinEye
                    await page.goto("https://tineye.com/", wait_until="networkidle", timeout=15000)
                    await asyncio.sleep(1)

                    # Upload file
                    file_input = await page.query_selector("input[type='file']")
                    if file_input:
                        await file_input.set_input_files(query.photo_path)

                        # Click search button
                        search_btn = await page.query_selector("button[type='submit'], .search-btn")
                        if search_btn:
                            await search_btn.click()

                        await asyncio.sleep(5)
                        await page.wait_for_load_state("networkidle", timeout=15000)

                        # Extract results
                        results = await page.query_selector_all(".match, .result")
                        for result in results[:10]:
                            img = await result.query_selector("img")
                            link = await result.query_selector("a.overlay, a.match-link")

                            img_src = ""
                            source_url = page.url

                            if img:
                                img_src = await img.get_attribute("src") or ""
                                if img_src.startswith("//"):
                                    img_src = "https:" + img_src

                            if link:
                                href = await link.get_attribute("href") or ""
                                if href:
                                    source_url = href

                            if img_src and img_src.startswith("http"):
                                candidates.append((img_src, source_url, "TinEye: image copy found"))

                except Exception as e:
                    print(f"  TinEye search error: {e}")
                finally:
                    await browser.close()

        except Exception as e:
            print(f"  Playwright (TinEye) unavailable: {e}")

        return candidates

    # =========================================================================
    # Wikidata/Wikimedia Commons Image Search
    # =========================================================================

    async def _wikidata_image_search(self, query: PersonQuery) -> list[tuple[str, str, str]]:
        """Search Wikidata for images of a person.

        Wikidata has structured data linking people to their images
        on Wikimedia Commons. High quality, official/public photos.
        """
        candidates: list[tuple[str, str, str]] = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Search Wikidata for the person
            search_queries = [
                query.full_name,
            ]
            if query.first_name and query.last_name:
                search_queries.append(f"{query.first_name} {query.last_name}")

            for search_term in search_queries[:2]:
                try:
                    # Wikidata search API (requires User-Agent)
                    resp = await client.get(
                        "https://www.wikidata.org/w/api.php",
                        headers={"User-Agent": "PersonIntelAgent/1.0 (contact@example.com)"},
                        params={
                            "action": "wbsearchentities",
                            "search": search_term,
                            "language": "en",
                            "format": "json",
                            "limit": 5,
                        }
                    )

                    if resp.status_code != 200:
                        print(f"  Wikidata search returned {resp.status_code}")
                        continue

                    data = resp.json()
                    for entity in data.get("search", []):
                        entity_id = entity.get("id", "")
                        entity_label = entity.get("label", "")
                        entity_desc = entity.get("description", "")

                        if not entity_id:
                            continue

                        # Get the entity's image (P18 property)
                        try:
                            entity_resp = await client.get(
                                f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json",
                                headers={"User-Agent": "PersonIntelAgent/1.0 (contact@example.com)"},
                            )
                            if entity_resp.status_code != 200:
                                continue

                            entity_data = entity_resp.json()
                            entities = entity_data.get("entities", {})
                            entity_info = entities.get(entity_id, {})
                            claims = entity_info.get("claims", {})

                            # P18 = image property
                            p18_claims = claims.get("P18", [])
                            for claim in p18_claims:
                                mainsnak = claim.get("mainsnak", {})
                                datavalue = mainsnak.get("datavalue", {})
                                image_name = datavalue.get("value", "")

                                if image_name:
                                    # Construct Wikimedia Commons URL
                                    encoded_name = urllib.parse.quote(image_name.replace(" ", "_"))
                                    # Get thumbnail URL from Commons
                                    commons_url = f"https://commons.wikimedia.org/wiki/File:{encoded_name}"

                                    # Use Wikimedia API to get actual image URL
                                    api_resp = await client.get(
                                        "https://commons.wikimedia.org/w/api.php",
                                        headers={"User-Agent": "PersonIntelAgent/1.0 (contact@example.com)"},
                                        params={
                                            "action": "query",
                                            "titles": f"File:{image_name}",
                                            "prop": "imageinfo",
                                            "iiprop": "url",
                                            "iiurlwidth": "800",
                                            "format": "json",
                                        }
                                    )
                                    if api_resp.status_code == 200:
                                        api_data = api_resp.json()
                                        pages = api_data.get("query", {}).get("pages", {})
                                        for page_id, page_info in pages.items():
                                            imageinfo = page_info.get("imageinfo", [{}])
                                            if imageinfo:
                                                img_url = imageinfo[0].get("thumburl") or imageinfo[0].get("url", "")
                                                if img_url:
                                                    candidates.append((
                                                        img_url,
                                                        commons_url,
                                                        f"Wikidata: {entity_label} ({entity_desc})"
                                                    ))

                        except Exception:
                            pass

                        await asyncio.sleep(0.5)

                except Exception as e:
                    print(f"  Wikidata search error: {e}")

            # Also try Wikimedia Commons search directly
            if query.first_name and query.last_name:
                try:
                    commons_search = f"{query.first_name} {query.last_name}"
                    resp = await client.get(
                        "https://commons.wikimedia.org/w/api.php",
                        headers={"User-Agent": "PersonIntelAgent/1.0 (contact@example.com)"},
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrsearch": f'"{commons_search}" filetype:bitmap',
                            "gsrnamespace": "6",  # File namespace
                            "prop": "imageinfo",
                            "iiprop": "url",
                            "iiurlwidth": "800",
                            "format": "json",
                            "gsrlimit": "5",
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        pages = data.get("query", {}).get("pages", {})
                        for page_id, page_info in pages.items():
                            title = page_info.get("title", "")
                            imageinfo = page_info.get("imageinfo", [{}])
                            if imageinfo:
                                img_url = imageinfo[0].get("thumburl") or imageinfo[0].get("url", "")
                                if img_url:
                                    page_url = f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title)}"
                                    candidates.append((img_url, page_url, "Wikimedia Commons: direct search"))
                except Exception:
                    pass

        return candidates

    # =========================================================================
    # Image Comparison
    # =========================================================================

    async def _check_image(
        self,
        client: httpx.AsyncClient,
        img_url: str,
        source_url: str,
        context: str,
        reference_path: str,
    ) -> Optional[ImageMatch]:
        """Download and compare image with reference."""
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
