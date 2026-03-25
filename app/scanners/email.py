"""Email Scanner — Pattern generation, SMTP verification, and breach checking."""

from __future__ import annotations

import asyncio
import smtplib
import socket
import dns.resolver
from typing import Optional

import httpx

from app.models import PersonQuery, SearchResult, Source, Confidence
from app.scanners.base import BaseScanner


class EmailScanner(BaseScanner):
    """Generate, verify, and check email addresses for breach exposure."""

    name = "email"
    description = "Email pattern generation, SMTP verification, and breach checking"

    # Common email patterns
    PATTERNS = [
        "{first}.{last}",       # john.smith
        "{first}{last}",        # johnsmith
        "{first}_{last}",       # john_smith
        "{f}{last}",            # jsmith
        "{first}{l}",           # johns
        "{f}.{last}",           # j.smith
        "{first}",              # john
        "{last}.{first}",       # smith.john
        "{last}{f}",            # smithj
        "{f}{l}",               # js
        "{first}-{last}",       # john-smith
    ]

    def __init__(self, hibp_api_key: Optional[str] = None):
        """Initialize with optional HIBP API key for breach checking."""
        self.hibp_api_key = hibp_api_key

    async def scan(self, query: PersonQuery) -> list[SearchResult]:
        """Run email scan: generate candidates, verify, check breaches."""
        results: list[SearchResult] = []

        # Step 1: Generate email candidates from common domains
        candidates = self._generate_candidates(query)
        if not candidates:
            return results

        # Step 2: Verify via SMTP (best-effort, don't fail hard)
        verified: list[str] = []
        for email in candidates[:6]:  # Keep runtime bounded for the webapp scanner pool
            try:
                is_valid = await asyncio.to_thread(self._smtp_check, email)
                if is_valid:
                    verified.append(email)
                    results.append(SearchResult(
                        source=Source.EMAIL,
                        title=f"Verified email: {email}",
                        url=f"mailto:{email}",
                        snippet="SMTP mailbox verified as existing",
                        confidence=Confidence.HIGH,
                    ))
            except Exception:
                # SMTP check failed — add as candidate anyway
                results.append(SearchResult(
                    source=Source.EMAIL,
                    title=f"Candidate email: {email}",
                    url=f"mailto:{email}",
                    snippet="Generated from common patterns (not verified)",
                    confidence=Confidence.LOW,
                ))

        # Step 3: Check HIBP for breached emails
        if self.hibp_api_key:
            check_list = verified if verified else candidates[:10]
            for email in check_list:
                try:
                    breaches = await self._check_hibp(email)
                    if breaches:
                        breach_names = ", ".join(b["Name"] for b in breaches[:5])
                        results.append(SearchResult(
                            source=Source.BREACH,
                            title=f"Breached: {email}",
                            url=f"https://haveibeenpwned.com/account/{email}",
                            snippet=f"Found in {len(breaches)} breach(es): {breach_names}",
                            confidence=Confidence.HIGH,
                        ))
                except Exception:
                    pass

        return results

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------

    def _generate_candidates(self, query: PersonQuery) -> list[str]:
        """Generate likely email addresses from name + common domains."""
        fn = (query.first_name or "").lower().strip()
        ln = (query.last_name or "").lower().strip()

        if not fn and not ln:
            # Try splitting full_name
            parts = query.full_name.lower().strip().split()
            if len(parts) >= 2:
                fn = parts[0]
                ln = parts[-1]

        if not fn and not ln:
            return list(query.emails)

        f = fn[0] if fn else ""
        l = ln[0] if ln else ""

        # Common email domains
        domains = [
            "gmail.com", "outlook.com", "hotmail.com", "yahoo.com",
            "protonmail.com", "icloud.com", "mail.com", "web.de",
            "gmx.de", "gmx.com", "t-online.de",
        ]

        candidates: list[str] = list(query.emails)  # Start with known emails

        for pattern in self.PATTERNS:
            local = pattern.format(
                first=fn, last=ln,
                f=f, l=l,
                FIRST=fn.capitalize(), LAST=ln.capitalize(),
            )
            if local and len(local) >= 2:  # Skip trivially short
                for domain in domains:
                    candidates.append(f"{local}@{domain}")

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique.append(c)

        return unique

    # ------------------------------------------------------------------
    # SMTP verification
    # ------------------------------------------------------------------

    def _smtp_check(self, email: str, timeout: int = 3) -> bool:
        """Verify an email address exists via SMTP RCPT TO.

        Connects to the MX server, sends HELO + MAIL FROM + RCPT TO,
        then checks if the server accepts the recipient. Does NOT send
        any actual email.
        """
        domain = email.split("@")[1] if "@" in email else ""
        if not domain:
            return False

        # Resolve MX records
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = timeout
            resolver.lifetime = timeout
            mx_records = resolver.resolve(domain, "MX")
            mx_hosts = sorted(
                [(r.preference, str(r.exchange).rstrip(".")) for r in mx_records]
            )
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
            return False

        if not mx_hosts:
            return False

        mx_host = mx_hosts[0][1]  # Lowest preference (highest priority)

        try:
            with smtplib.SMTP(mx_host, 25, timeout=timeout) as smtp:
                smtp.ehlo_or_helo_if_needed()
                smtp.mail("check@example.com")
                code, _ = smtp.rcpt(email)
                smtp.quit()
                # 250 = OK, 251 = User not local, will forward
                return code in (250, 251)
        except (smtplib.SMTPException, socket.error, socket.timeout, OSError):
            return False

    # ------------------------------------------------------------------
    # Have I Been Pwned
    # ------------------------------------------------------------------

    async def _check_hibp(self, email: str) -> list[dict]:
        """Check Have I Been Pwned for breach data on an email.

        Requires a HIBP API key (paid, ~$3.50/month).
        GET https://haveibeenpwned.com/api/v3/breachedaccount/{email}
        """
        if not self.hibp_api_key:
            return []

        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{urllib.parse.quote(email)}"
        headers = {
            "hibp-api-key": self.hibp_api_key,
            "user-agent": "PersonIntelAgent",
        }

        import urllib.parse

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, params={"truncateResponse": "true"})

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                return []  # No breaches found
            elif resp.status_code == 429:
                # Rate limited — wait and retry once
                await asyncio.sleep(2)
                resp = await client.get(url, headers=headers, params={"truncateResponse": "true"})
                if resp.status_code == 200:
                    return resp.json()
            return []
