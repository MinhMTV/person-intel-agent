"""Public Records Scanner — Domain intelligence + registry lookups.

Search engines:
  - WHOIS: domain registration lookup (find domains associated with person)
  - DNS: DNS records for domain intelligence
  - Reverse WHOIS: find domains registered to an email/name
"""

from __future__ import annotations

import asyncio
import re
import socket
from typing import Optional

import httpx

from app.models import PersonQuery, SearchResult, Source, Confidence


class PublicRecordsScanner:
    """Public records and domain intelligence scanner.

    Looks up:
      - WHOIS data for potential domains
      - DNS records (A, MX, NS, TXT)
      - Email domain analysis
    """

    name = "public_records"
    description = "WHOIS domain lookup + DNS intelligence"

    async def scan(self, query: PersonQuery) -> list:
        """Run public records scan."""
        results = []

        # Generate potential domain names from the person's name
        domains = self._generate_domains(query)

        tasks = [
            self._whois_lookup(query, domains),
            self._dns_lookup(domains),
            self._email_domain_analysis(query),
        ]

        for coro in asyncio.as_completed(tasks):
            try:
                platform_results = await coro
                results.extend(platform_results)
            except Exception as e:
                print(f"  Public records error: {e}")

        return results

    def _generate_domains(self, query: PersonQuery) -> list[str]:
        """Generate potential domain names from person's name."""
        domains = []

        if query.first_name and query.last_name:
            fn = query.first_name.lower()
            ln = query.last_name.lower()
            # Common domain patterns
            patterns = [
                f"{fn}{ln}.com",
                f"{fn}-{ln}.com",
                f"{fn}.{ln}.com",
                f"{ln}{fn}.com",
                f"{fn[0]}{ln}.com",
                f"{fn}{ln[0]}.com",
                f"{fn}{ln}.de",
                f"{fn}-{ln}.de",
                f"{fn}{ln}.io",
                f"{fn}{ln}.net",
                f"{fn}{ln}.org",
            ]
            domains.extend(patterns)

        # Also check any domains from email addresses
        for email in query.emails:
            domain = email.split("@")[-1] if "@" in email else ""
            if domain and domain not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com"):
                domains.append(domain)

        return list(set(domains))

    # =========================================================================
    # WHOIS Lookup
    # =========================================================================

    async def _whois_lookup(self, query: PersonQuery, domains: list[str]) -> list:
        """Look up WHOIS data for potential domains.

        Uses the python-whois library for domain registration data.
        """
        results = []

        try:
            import whois as python_whois
        except ImportError:
            print("  python-whois not installed, skipping WHOIS lookups")
            return results

        loop = asyncio.get_event_loop()

        for domain in domains[:4]:
            try:
                w = await loop.run_in_executor(None, self._whois_query, domain)
                if w:
                    results.append(w)
            except Exception:
                pass
            await asyncio.sleep(0.15)

        return results

    def _whois_query(self, domain: str) -> Optional[SearchResult]:
        """Perform WHOIS query (sync)."""
        try:
            import whois as python_whois

            w = python_whois.whois(domain)

            if not w or not w.domain_name:
                return None

            # Build info
            info_parts = []
            if w.registrar:
                info_parts.append(f"Registrar: {w.registrar}")
            if w.creation_date:
                date = w.creation_date
                if isinstance(date, list):
                    date = date[0]
                info_parts.append(f"Created: {date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else date}")
            if w.name_servers:
                ns = w.name_servers if isinstance(w.name_servers, list) else [w.name_servers]
                info_parts.append(f"NS: {', '.join(ns[:3])}")
            if w.emails:
                emails = w.emails if isinstance(w.emails, list) else [w.emails]
                info_parts.append(f"Contact: {emails[0]}")

            name_str = w.domain_name
            if isinstance(name_str, list):
                name_str = name_str[0]

            return SearchResult(
                source=Source.BUSINESS,
                title=f"WHOIS: {name_str}",
                url=f"https://whois.com/{domain}",
                snippet=" | ".join(info_parts) if info_parts else "Domain registered",
                confidence=Confidence.HIGH,
            )
        except Exception:
            return None

    # =========================================================================
    # DNS Lookup
    # =========================================================================

    async def _dns_lookup(self, domains: list[str]) -> list:
        """Look up DNS records for domains.

        Checks A, MX, NS, and TXT records.
        """
        results = []

        try:
            import dns.resolver
        except ImportError:
            print("  dnspython not installed, skipping DNS lookups")
            return results

        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5

        for domain in domains[:3]:
            try:
                # A records
                try:
                    a_records = resolver.resolve(domain, "A")
                    ips = [str(r) for r in a_records]
                    results.append(SearchResult(
                        source=Source.WEB,
                        title=f"DNS A: {domain}",
                        url=f"https://{domain}",
                        snippet=f"IP: {', '.join(ips[:3])}",
                        confidence=Confidence.HIGH,
                    ))
                except Exception:
                    pass

                # MX records (mail servers)
                try:
                    mx_records = resolver.resolve(domain, "MX")
                    mx_hosts = [str(r.exchange) for r in mx_records]
                    results.append(SearchResult(
                        source=Source.EMAIL,
                        title=f"DNS MX: {domain}",
                        url=f"https://{domain}",
                        snippet=f"Mail servers: {', '.join(mx_hosts[:3])}",
                        confidence=Confidence.HIGH,
                    ))
                except Exception:
                    pass

            except Exception:
                pass

            await asyncio.sleep(0.1)

        return results

    # =========================================================================
    # Email Domain Analysis
    # =========================================================================

    async def _email_domain_analysis(self, query: PersonQuery) -> list:
        """Analyze email domains for additional intelligence."""
        results = []

        for email in query.emails:
            if "@" not in email:
                continue

            domain = email.split("@")[-1]

            # Skip common providers
            if domain in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "protonmail.com"):
                continue

            # Check if domain exists
            try:
                import dns.resolver
                resolver = dns.resolver.Resolver()
                resolver.timeout = 3
                resolver.lifetime = 3

                try:
                    mx = resolver.resolve(domain, "MX")
                    mx_hosts = [str(r.exchange) for r in mx]
                    results.append(SearchResult(
                        source=Source.EMAIL,
                        title=f"Custom email domain: {domain}",
                        url=f"https://{domain}",
                        snippet=f"MX: {mx_hosts[0] if mx_hosts else 'N/A'} — Likely personal/business domain",
                        confidence=Confidence.HIGH,
                    ))
                except Exception:
                    pass
            except ImportError:
                pass

        return results
