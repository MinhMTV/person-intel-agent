"""Professional Intelligence Scanner — Academic + business + patent search.

Search engines:
  - Google Scholar: publications, citations, h-index
  - ORCID: researcher identifier lookup
  - Google Patents: patent search by inventor name
  - Crunchbase-style company lookup (via web search)
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models import PersonQuery, SearchResult, SocialProfile, Source, Confidence


class ProfessionalIntelScanner:
    """Professional and academic intelligence gathering.

    Searches:
      - Google Scholar for academic publications and citations
      - ORCID for researcher profiles
      - Google Patents for patent filings
      - Company/startup associations
    """

    name = "professional_intel"
    description = "Google Scholar + ORCID + Patents + company lookup"

    async def scan(self, query: PersonQuery) -> list:
        """Run professional intelligence scan."""
        results = []

        tasks = [
            self._search_google_scholar(query),
            self._search_orcid(query),
            self._search_google_patents(query),
        ]

        for coro in asyncio.as_completed(tasks):
            try:
                platform_results = await coro
                results.extend(platform_results)
            except Exception as e:
                print(f"  Professional intel error: {e}")

        return results

    # =========================================================================
    # Google Scholar
    # =========================================================================

    async def _search_google_scholar(self, query: PersonQuery) -> list:
        """Search Google Scholar for academic publications.

        Extracts: paper titles, citation counts, year, co-authors.
        Scrapes the Scholar search results page.
        """
        results = []

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            },
        ) as client:
            search_queries = []
            if query.first_name and query.last_name:
                search_queries.append(f'"{query.first_name} {query.last_name}"')
                search_queries.append(f"{query.first_name} {query.last_name}")
            search_queries.append(f'"{query.full_name}"')

            for search_term in search_queries[:2]:
                try:
                    encoded = urllib.parse.quote_plus(search_term)
                    url = f"https://scholar.google.com/scholar?q={encoded}&hl=en"

                    resp = await client.get(url)
                    if resp.status_code != 200:
                        await asyncio.sleep(2)
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")

                    # Parse search results
                    for item in soup.select(".gs_ri")[:5]:
                        title_el = item.select_one(".gs_rt a")
                        if not title_el:
                            continue

                        title = title_el.get_text(strip=True)
                        link = title_el.get("href", "")

                        # Citation count
                        citation_el = item.select_one(".gs_fl a[href*='cites']")
                        citations = 0
                        if citation_el:
                            cit_text = citation_el.get_text(strip=True)
                            cit_match = re.search(r"Cited by (\d+)", cit_text)
                            if cit_match:
                                citations = int(cit_match.group(1))

                        # Year and authors from snippet
                        snippet_el = item.select_one(".gs_a")
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                        # Extract year
                        year_match = re.search(r"\b(19|20)\d{2}\b", snippet)
                        year = year_match.group(0) if year_match else ""

                        # Snippet text
                        desc_el = item.select_one(".gs_rs")
                        description = desc_el.get_text(strip=True) if desc_el else ""

                        # Build title with metadata
                        full_title = title
                        if citations > 0:
                            full_title += f" [{citations} citations]"
                        if year:
                            full_title += f" ({year})"

                        results.append(SearchResult(
                            source=Source.ACADEMIC,
                            title=full_title,
                            url=link,
                            snippet=f"{snippet} — {description[:100]}" if description else snippet,
                            confidence=Confidence.MEDIUM,
                        ))

                    # Also try to find the author's Scholar profile
                    profile_links = soup.select("a[href*='user=']")
                    for profile_link in profile_links[:1]:
                        href = profile_link.get("href", "")
                        if href:
                            profile_url = f"https://scholar.google.com{href}" if href.startswith("/") else href
                            profile_name = profile_link.get_text(strip=True)

                            results.append(SocialProfile(
                                platform="google_scholar",
                                url=profile_url,
                                display_name=profile_name,
                                confidence=Confidence.MEDIUM,
                            ))

                except Exception as e:
                    print(f"  Google Scholar error: {e}")

                await asyncio.sleep(2)  # Rate limit Scholar

        return results

    # =========================================================================
    # ORCID Lookup
    # =========================================================================

    async def _search_orcid(self, query: PersonQuery) -> list:
        """Search ORCID for researcher profiles.

        ORCID provides persistent identifiers for researchers.
        The API is public and returns structured data.
        """
        results = []

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            search_names = []
            if query.first_name and query.last_name:
                search_names.append(f"{query.first_name} {query.last_name}")
            search_names.append(query.full_name)

            for name in search_names[:2]:
                try:
                    # ORCID public API search
                    resp = await client.get(
                        "https://pub.orcid.org/v3.0/search/",
                        params={
                            "q": f'family-name:"{query.last_name or name}" AND given-names:"{query.first_name or name}"',
                            "rows": 5,
                        },
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "PersonIntelAgent/1.0",
                        },
                    )

                    if resp.status_code != 200:
                        # Try simpler search
                        resp = await client.get(
                            "https://pub.orcid.org/v3.0/search/",
                            params={"q": f'"{name}"', "rows": 5},
                            headers={
                                "Accept": "application/json",
                                "User-Agent": "PersonIntelAgent/1.0",
                            },
                        )

                    if resp.status_code == 200:
                        data = resp.json()
                        for result in (data.get("result") or []):
                            orcid_id = result.get("orcid-identifier", {}).get("path", "")
                            if not orcid_id:
                                continue

                            # Get detailed profile
                            profile_resp = await client.get(
                                f"https://pub.orcid.org/v3.0/{orcid_id}",
                                headers={
                                    "Accept": "application/json",
                                    "User-Agent": "PersonIntelAgent/1.0",
                                },
                            )

                            if profile_resp.status_code == 200:
                                profile = profile_resp.json()
                                person = profile.get("person") or {}

                                # Name
                                name_data = person.get("name") or {}
                                given = (name_data.get("given-names") or {}).get("value", "")
                                family = (name_data.get("family-name") or {}).get("value", "")
                                full_name = f"{given} {family}".strip()

                                # Bio
                                bio_el = person.get("biography") or {}
                                bio = bio_el.get("content", "") if bio_el else ""

                                # Employment
                                activities = profile.get("activities-summary") or {}
                                employments_group = ((activities.get("employments") or {}).get("affiliation-group") or [])
                                employment_str = ""
                                if employments_group:
                                    summaries = employments_group[0].get("summaries") or []
                                    emp = (summaries[0].get("employment-summary") or {}) if summaries else {}
                                    org = (emp.get("organization") or {}).get("name", "")
                                    role = emp.get("role-title", "")
                                    if org:
                                        employment_str = f"{role} at {org}" if role else org

                                # Build bio
                                bio_parts = []
                                if employment_str:
                                    bio_parts.append(employment_str)
                                if bio:
                                    bio_parts.append(bio[:100])

                                results.append(SocialProfile(
                                    platform="orcid",
                                    url=f"https://orcid.org/{orcid_id}",
                                    username=orcid_id,
                                    display_name=full_name or name,
                                    bio=" | ".join(bio_parts) if bio_parts else None,
                                    confidence=Confidence.HIGH if full_name else Confidence.MEDIUM,
                                ))

                except Exception as e:
                    print(f"  ORCID error: {e}")

                await asyncio.sleep(1)

        return results

    # =========================================================================
    # Google Patents
    # =========================================================================

    async def _search_google_patents(self, query: PersonQuery) -> list:
        """Search Google Patents for inventions by the person.

        Extracts: patent title, filing date, abstract snippet, assignee.
        Uses Google Patents search page scraping.
        """
        results = []

        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            },
        ) as client:
            search_names = []
            if query.first_name and query.last_name:
                search_names.append(f'inventor:"{query.last_name}, {query.first_name}"')
                search_names.append(f'inventor:"{query.first_name} {query.last_name}"')
            search_names.append(f'inventor:"{query.full_name}"')

            for search_term in search_names[:2]:
                try:
                    encoded = urllib.parse.quote_plus(search_term)
                    url = f"https://patents.google.com/?q={encoded}&oq={encoded}"

                    resp = await client.get(url)
                    if resp.status_code != 200:
                        await asyncio.sleep(2)
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")

                    # Parse patent results
                    for item in soup.select("section.results .result-item, .search-result-item")[:5]:
                        title_el = item.select_one("h3 a, .result-title a")
                        if not title_el:
                            continue

                        title = title_el.get_text(strip=True)
                        link = title_el.get("href", "")
                        if link and not link.startswith("http"):
                            link = f"https://patents.google.com{link}"

                        # Snippet/abstract
                        snippet_el = item.select_one(".result-snippet, .abstract")
                        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                        # Assignee
                        assignee_el = item.select_one(".assignee, .result-assignee")
                        assignee = assignee_el.get_text(strip=True) if assignee_el else ""

                        # Date
                        date_el = item.select_one(".dates, .result-date")
                        date_str = date_el.get_text(strip=True) if date_el else ""

                        title_extra = ""
                        if assignee:
                            title_extra += f" [{assignee}]"
                        if date_str:
                            title_extra += f" ({date_str})"

                        results.append(SearchResult(
                            source=Source.BUSINESS,
                            title=f"Patent: {title}{title_extra}",
                            url=link,
                            snippet=snippet[:200] if snippet else None,
                            confidence=Confidence.HIGH,
                        ))

                except Exception as e:
                    print(f"  Google Patents error: {e}")

                await asyncio.sleep(2)

        return results
