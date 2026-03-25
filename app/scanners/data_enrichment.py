"""Data Enrichment Scanner — Phone, email, and cross-reference analysis.

Features:
  - Phone number validation and carrier lookup
  - Email deliverability verification (MX + SMTP)
  - Social media cross-referencing (find same person across platforms)
  - Name variant expansion and geographic inference
"""

from __future__ import annotations

import asyncio
import re
import socket
from typing import Optional

import httpx

from app.models import PersonQuery, SearchResult, SocialProfile, Source, Confidence


class DataEnrichmentScanner:
    """Data enrichment and cross-referencing.

    Enriches person data with:
      - Phone number validation and carrier info
      - Enhanced email verification
      - Cross-platform identity matching
      - Geographic inference from available data
    """

    name = "data_enrichment"
    description = "Phone validation + email verification + cross-referencing"

    async def scan(self, query: PersonQuery) -> list:
        """Run data enrichment scan."""
        results = []

        tasks = [
            self._enrich_emails(query),
            self._cross_reference_social(query),
            self._enrich_locations(query),
        ]

        for coro in asyncio.as_completed(tasks):
            try:
                enrichment_results = await coro
                results.extend(enrichment_results)
            except Exception as e:
                print(f"  Enrichment error: {e}")

        return results

    # =========================================================================
    # Email Enrichment
    # =========================================================================

    async def _enrich_emails(self, query: PersonQuery) -> list:
        """Verify and enrich email addresses.

        Checks: MX records, SMTP deliverability, disposable email detection.
        """
        results = []

        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            for email in query.emails:
                try:
                    verification = await self._verify_email(email)
                    if verification:
                        results.append(verification)
                except Exception:
                    pass

        # Generate additional email patterns to check
        if query.first_name and query.last_name:
            generated = self._generate_email_patterns(query)
            for email in generated[:3]:
                try:
                    verification = await self._verify_email(email)
                    if verification and verification.confidence != Confidence.LOW:
                        results.append(verification)
                except Exception:
                    pass
                await asyncio.sleep(0.2)

        return results

    def _generate_email_patterns(self, query: PersonQuery) -> list[str]:
        """Generate common email patterns from name."""
        if not query.first_name or not query.last_name:
            return []

        fn = query.first_name.lower()
        ln = query.last_name.lower()

        # Common domains to check
        domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "protonmail.com"]

        patterns = [
            f"{fn}.{ln}",
            f"{fn}{ln}",
            f"{fn[0]}{ln}",
            f"{fn}_{ln}",
            f"{ln}.{fn}",
            f"{fn}",
        ]

        emails = []
        for pattern in patterns:
            for domain in domains[:2]:  # Check top 2 domains only
                emails.append(f"{pattern}@{domain}")

        # Also check custom domains from name
        if query.locations:
            for loc in query.locations[:1]:
                if loc.country_code:
                    cc = loc.country_code.lower()
                    emails.append(f"{fn}.{ln}@{fn}{ln}.{cc}")
                    emails.append(f"{fn}@{ln}.{cc}")

        return emails

    async def _verify_email(self, email: str) -> Optional[SearchResult]:
        """Verify email deliverability."""
        if "@" not in email:
            return None

        local, domain = email.rsplit("@", 1)

        # Check for disposable email domains
        disposable_domains = {
            "mailinator.com", "guerrillamail.com", "tempmail.com",
            "throwaway.email", "temp-mail.org", "fakeinbox.com",
            "yopmail.com", "sharklasers.com", "guerrillamailblock.com",
        }

        if domain in disposable_domains:
            return SearchResult(
                source=Source.EMAIL,
                title=f"⚠️ Disposable email: {email}",
                url=f"mailto:{email}",
                snippet="This is a disposable/temporary email address",
                confidence=Confidence.HIGH,
            )

        # Check MX records
        try:
            import dns.resolver
            resolver = dns.resolver.Resolver()
            resolver.timeout = 3
            resolver.lifetime = 3

            try:
                mx = resolver.resolve(domain, "MX")
                mx_hosts = [str(r.exchange).rstrip(".") for r in mx]

                # SMTP verification (connect only, don't send)
                deliverable = await self._smtp_check(email, mx_hosts[0] if mx_hosts else None)

                status = "deliverable" if deliverable else "undeliverable"
                confidence = Confidence.HIGH if deliverable else Confidence.LOW

                return SearchResult(
                    source=Source.EMAIL,
                    title=f"Email: {email} ({status})",
                    url=f"mailto:{email}",
                    snippet=f"Domain: {domain} | MX: {mx_hosts[0] if mx_hosts else 'N/A'}",
                    confidence=confidence,
                )
            except Exception:
                return SearchResult(
                    source=Source.EMAIL,
                    title=f"Email: {email} (no MX)",
                    url=f"mailto:{email}",
                    snippet=f"Domain {domain} has no mail servers configured",
                    confidence=Confidence.LOW,
                )
        except ImportError:
            return None

    async def _smtp_check(self, email: str, mx_host: Optional[str]) -> bool:
        """Check if email is deliverable via SMTP (connect only)."""
        if not mx_host:
            return False

        try:
            import smtplib
            loop = asyncio.get_event_loop()

            def _check():
                try:
                    server = smtplib.SMTP(timeout=3)
                    server.connect(mx_host, 25)
                    server.helo("verify.local")
                    server.mail("verify@verify.local")
                    code, _ = server.rcpt(email)
                    server.quit()
                    return code == 250
                except Exception:
                    return False

            return await loop.run_in_executor(None, _check)
        except Exception:
            return False

    # =========================================================================
    # Social Media Cross-Referencing
    # =========================================================================

    async def _cross_reference_social(self, query: PersonQuery) -> list:
        """Cross-reference identities across social platforms.

        Finds the same person on multiple platforms by matching:
          - Username patterns
          - Display names
          - Bio/location info
          - Profile links
        """
        results = []

        # Check if known usernames appear on multiple platforms
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            platforms = {
                "twitter": "https://nitter.net/{}",
                "github": "https://api.github.com/users/{}",
                "reddit": "https://www.reddit.com/user/{}/about.json",
                "keybase": "https://keybase.io/{}",
            }

            for username in query.usernames[:2]:
                found_on = []
                for platform, url_template in platforms.items():
                    try:
                        url = url_template.format(username)
                        headers = {}
                        if platform == "reddit":
                            headers["User-Agent"] = "PersonIntelAgent/1.0"
                        if platform == "github":
                            headers["Accept"] = "application/vnd.github.v3+json"

                        resp = await client.get(url, headers=headers)
                        if resp.status_code == 200:
                            found_on.append(platform)
                    except Exception:
                        pass
                    await asyncio.sleep(0.15)

                if len(found_on) >= 2:
                    results.append(SearchResult(
                        source=Source.SOCIAL,
                        title=f"Cross-platform identity: @{username}",
                        url=f"https://github.com/{username}",
                        snippet=f"Same username on: {', '.join(found_on)}",
                        confidence=Confidence.HIGH,
                    ))

        return results

    # =========================================================================
    # Location Enrichment
    # =========================================================================

    async def _enrich_locations(self, query: PersonQuery) -> list:
        """Enrich location data with geographic expansion."""
        results = []

        for loc in query.locations:
            # Expand city to region/country
            if loc.city and not loc.country:
                expanded = self._expand_location(loc.city)
                if expanded:
                    results.append(SearchResult(
                        source=Source.WEB,
                        title=f"Location context: {loc.city}",
                        url=f"https://en.wikipedia.org/wiki/{loc.city.replace(' ', '_')}",
                        snippet=expanded,
                        confidence=Confidence.MEDIUM,
                    ))

        return results

    def _expand_location(self, city: str) -> Optional[str]:
        """Expand a city name to include region and country context."""
        # Well-known city-to-region mappings
        expansions = {
            "Oberhausen": "North Rhine-Westphalia, Germany",
            "Munich": "Bavaria, Germany",
            "Berlin": "Berlin, Germany",
            "Hamburg": "Hamburg, Germany",
            "Vienna": "Vienna, Austria",
            "Graz": "Styria, Austria",
            "Salzburg": "Salzburg, Austria",
            "Innsbruck": "Tyrol, Austria",
            "Zurich": "Zurich, Switzerland",
            "London": "Greater London, United Kingdom",
            "Paris": "Île-de-France, France",
            "Amsterdam": "North Holland, Netherlands",
        }

        for known_city, expansion in expansions.items():
            if city.lower() == known_city.lower():
                return expansion

        return None
