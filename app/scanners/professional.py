"""LinkedIn & Xing Scrapers — Uses Playwright browser sessions for authenticated access."""

from __future__ import annotations
import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx

from app.models import PersonQuery, SearchResult, SocialProfile, ImageMatch, Source, Confidence
from app.login_manager import load_cookies, save_cookies


class LinkedInScraper:
    """Scrape LinkedIn profiles and images using Playwright with stored session."""

    name = "linkedin"
    description = "LinkedIn profile scraper (requires session cookies)"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self._browser = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().__aenter__()
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.__aexit__(*args)

    async def login_and_save_cookies(self) -> bool:
        """Open browser for manual login, then save cookies."""
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)  # Visible for manual login
            context = await browser.new_context()
            page = await context.new_page()

            print("🔗 Opening LinkedIn login page...")
            await page.goto("https://www.linkedin.com/login", wait_until="networkidle")
            print("👆 Please log in manually in the browser window.")
            print("   Press Enter here after you've logged in...")

            # Wait for user to log in
            await asyncio.get_event_loop().run_in_executor(None, input)

            # Check if logged in
            if "feed" in page.url or "mynetwork" in page.url:
                cookies = await context.cookies()
                save_cookies("linkedin", cookies)
                print("✅ LinkedIn cookies saved")
                await browser.close()
                return True
            else:
                print("❌ Login not detected. Please try again.")
                await browser.close()
                return False

    async def _get_context(self):
        """Create a browser context with saved cookies."""
        from playwright.async_api import async_playwright
        if not self._pw:
            self._pw = await async_playwright().__aenter__()

        self._browser = await self._pw.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Load cookies if available
        cookies = load_cookies("linkedin")
        if cookies:
            await context.add_cookies(cookies)

        return context

    async def search_profiles(self, query: PersonQuery) -> list[tuple[str, str, str, str]]:
        """Search LinkedIn for profiles.
        Returns list of (profile_url, name, headline, image_url).
        """
        results = []
        context = await self._get_context()
        page = await context.new_page()

        search_terms = [
            f'"{query.full_name}"',
            query.full_name,
        ]
        if query.locations:
            search_terms.append(f'{query.full_name} {query.locations[0].raw}')

        for term in search_terms[:2]:
            try:
                import urllib.parse
                encoded = urllib.parse.quote_plus(term)
                url = f"https://www.linkedin.com/search/results/people/?keywords={encoded}"

                await page.goto(url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(2)

                # Check if logged in
                if "login" in page.url or "authwall" in page.url:
                    print("⚠️ LinkedIn: not logged in. Run login_and_save_cookies() first.")
                    break

                # Extract profile cards
                cards = await page.query_selector_all("li.reusable-search__result-container")
                for card in cards[:5]:
                    try:
                        # Profile link
                        link_el = await card.query_selector("a.app-aware-link")
                        profile_url = await link_el.get_attribute("href") if link_el else ""
                        if profile_url and "?" in profile_url:
                            profile_url = profile_url.split("?")[0]

                        # Name
                        name_el = await card.query_selector("span.entity-result__title-text")
                        name = await name_el.inner_text() if name_el else ""

                        # Headline
                        headline_el = await card.query_selector("div.entity-result__primary-subtitle")
                        headline = await headline_el.inner_text() if headline_el else ""

                        # Image
                        img_el = await card.query_selector("img.presence-entity__image")
                        img_url = await img_el.get_attribute("src") if img_el else ""

                        if profile_url:
                            results.append((profile_url, name.strip(), headline.strip(), img_url))
                    except Exception:
                        pass

                await asyncio.sleep(2)
            except Exception as e:
                print(f"  LinkedIn search error: {e}")

        await page.close()
        return results

    async def scrape_profile(self, profile_url: str) -> dict:
        """Scrape a single LinkedIn profile for details."""
        context = await self._get_context()
        page = await context.new_page()

        try:
            await page.goto(profile_url, wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)

            data = {"url": profile_url}

            # Name
            name_el = await page.query_selector("h1.text-heading-xlarge")
            if name_el:
                data["name"] = await name_el.inner_text()

            # Headline
            headline_el = await page.query_selector("div.text-body-medium")
            if headline_el:
                data["headline"] = await headline_el.inner_text()

            # Location
            location_el = await page.query_selector("span.text-body-small.inline")
            if location_el:
                data["location"] = await location_el.inner_text()

            # Profile image
            img_el = await page.query_selector("img.pv-top-card-profile-picture__image--show")
            if img_el:
                data["image_url"] = await img_el.get_attribute("src")

            # About section
            about_section = await page.query_selector("#about")
            if about_section:
                about_text = await about_section.inner_text()
                data["about"] = about_text[:500]

            return data
        except Exception as e:
            print(f"  LinkedIn profile scrape error: {e}")
            return {"url": profile_url, "error": str(e)}
        finally:
            await page.close()


class XingScraper:
    """Scrape Xing profiles and images using Playwright with stored session."""

    name = "xing"
    description = "Xing profile scraper (requires session cookies)"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self._browser = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().__aenter__()
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.__aexit__(*args)

    async def login_and_save_cookies(self) -> bool:
        """Open browser for manual login, then save cookies."""
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()

            print("🔗 Opening Xing login page...")
            await page.goto("https://www.xing.com/login", wait_until="networkidle")
            print("👆 Please log in manually in the browser window.")
            print("   Press Enter here after you've logged in...")

            await asyncio.get_event_loop().run_in_executor(None, input)

            if "feed" in page.url or "mynetwork" in page.url or "xing.com" in page.url:
                cookies = await context.cookies()
                save_cookies("xing", cookies)
                print("✅ Xing cookies saved")
                await browser.close()
                return True
            else:
                print("❌ Login not detected.")
                await browser.close()
                return False

    async def _get_context(self):
        """Create a browser context with saved cookies."""
        from playwright.async_api import async_playwright
        if not self._pw:
            self._pw = await async_playwright().__aenter__()

        self._browser = await self._pw.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        cookies = load_cookies("xing")
        if cookies:
            await context.add_cookies(cookies)

        return context

    async def search_profiles(self, query: PersonQuery) -> list[tuple[str, str, str, str]]:
        """Search Xing for profiles.
        Returns list of (profile_url, name, headline, image_url).
        """
        results = []
        context = await self._get_context()
        page = await context.new_page()

        try:
            import urllib.parse
            encoded = urllib.parse.quote_plus(query.full_name)
            url = f"https://www.xing.com/search/members?keywords={encoded}"

            await page.goto(url, wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)

            cards = await page.query_selector_all("li[class*='result']")
            for card in cards[:5]:
                try:
                    link_el = await card.query_selector("a[href*='/profile/']")
                    profile_url = await link_el.get_attribute("href") if link_el else ""
                    if profile_url and not profile_url.startswith("http"):
                        profile_url = f"https://www.xing.com{profile_url}"

                    name_el = await card.query_selector("h3, [class*='name']")
                    name = await name_el.inner_text() if name_el else ""

                    img_el = await card.query_selector("img")
                    img_url = await img_el.get_attribute("src") if img_el else ""

                    if profile_url:
                        results.append((profile_url, name.strip(), "", img_url))
                except Exception:
                    pass
        except Exception as e:
            print(f"  Xing search error: {e}")
        finally:
            await page.close()

        return results

    async def scrape_profile(self, profile_url: str) -> dict:
        """Scrape a single Xing profile."""
        context = await self._get_context()
        page = await context.new_page()

        try:
            await page.goto(profile_url, wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)

            data = {"url": profile_url}

            name_el = await page.query_selector("h1")
            if name_el:
                data["name"] = await name_el.inner_text()

            img_el = await page.query_selector("img[class*='profile'], img[class*='avatar']")
            if img_el:
                data["image_url"] = await img_el.get_attribute("src")

            return data
        except Exception as e:
            return {"url": profile_url, "error": str(e)}
        finally:
            await page.close()


class ProfessionalScanner:
    """Combined LinkedIn + Xing scanner for professional network search."""

    name = "professional"
    description = "LinkedIn + Xing profile search and scraping"

    def __init__(self, headless: bool = True):
        self.linkedin = LinkedInScraper(headless=headless)
        self.xing = XingScraper(headless=headless)

    async def scan(self, query: PersonQuery) -> list:
        """Search both platforms and return results."""
        results = []

        # LinkedIn
        try:
            async with self.linkedin:
                li_profiles = await self.linkedin.search_profiles(query)
                for url, name, headline, img_url in li_profiles:
                    results.append(SocialProfile(
                        platform="linkedin",
                        url=url,
                        display_name=name,
                        bio=headline,
                        confidence=Confidence.MEDIUM,
                    ))
                    if img_url:
                        results.append(ImageMatch(
                            source_url=url,
                            image_url=img_url,
                            similarity_score=0.0,  # Will be scored by face recognition
                            context=f"LinkedIn: {name}",
                        ))
        except Exception as e:
            print(f"LinkedIn error: {e}")

        # Xing
        try:
            async with self.xing:
                xing_profiles = await self.xing.search_profiles(query)
                for url, name, headline, img_url in xing_profiles:
                    results.append(SocialProfile(
                        platform="xing",
                        url=url,
                        display_name=name,
                        bio=headline,
                        confidence=Confidence.MEDIUM,
                    ))
                    if img_url:
                        results.append(ImageMatch(
                            source_url=url,
                            image_url=img_url,
                            similarity_score=0.0,
                            context=f"Xing: {name}",
                        ))
        except Exception as e:
            print(f"Xing error: {e}")

        return results

    async def login_linkedin(self):
        """Open browser for LinkedIn login."""
        return await self.linkedin.login_and_save_cookies()

    async def login_xing(self):
        """Open browser for Xing login."""
        return await self.xing.login_and_save_cookies()
