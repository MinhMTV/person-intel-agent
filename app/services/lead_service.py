"""Leads: email addresses and domains worth following up.

Replaces the old EmailScanner / PublicRecordsScanner behaviour:

* generated email addresses and name-derived domains are HYPOTHESES
  (``GENERATED_EMAIL_CANDIDATE`` / ``DOMAIN_EXISTS_UNVERIFIED``), never discoveries;
  generation is disabled by default
* SMTP mailbox probing is disabled by default (``SMTP_VERIFICATION_ENABLED``)
* the existence of ``janedoe.com`` does not attribute it to Jane Doe; only an
  explicit link from a candidate's own profile does (and that is recorded as
  cross-link evidence during clustering, not here)
"""

from __future__ import annotations

import asyncio
import smtplib
import socket
from urllib.parse import quote

import httpx

from app.config import Settings
from app.domain.candidate import CandidateIdentity, CandidatePage
from app.domain.identity import IdentityHints
from app.domain.investigation import Lead, LeadKind, LeadStatus
from app.infrastructure.security.url_guard import is_ip_allowed
from app.utils.canonical import normalize_email, normalize_name, registrable_domain
from app.utils.platforms import SOCIAL_DOMAINS, platform_for

_FREEMAIL = ["gmail.com", "outlook.com", "gmx.de", "web.de", "yahoo.com"]
_PATTERNS = ["{f}.{l}", "{f}{l}", "{fi}{l}", "{f}_{l}", "{l}.{f}"]


def generate_email_candidates(name: str | None, limit: int = 15) -> list[str]:
    tokens = normalize_name(name).split()
    if len(tokens) < 2:
        return []
    first, last = tokens[0], tokens[-1]
    out = []
    for pattern in _PATTERNS:
        local = pattern.format(f=first, l=last, fi=first[0])
        for domain in _FREEMAIL:
            out.append(f"{local}@{domain}")
    return out[:limit]


def generate_domain_candidates(name: str | None) -> list[str]:
    tokens = normalize_name(name).split()
    if len(tokens) < 2:
        return []
    first, last = tokens[0], tokens[-1]
    return [f"{first}{last}.com", f"{first}-{last}.com", f"{first}{last}.de", f"{first}{last}.net"]


class LeadService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def collect(
        self, hints: IdentityHints, pages: list[CandidatePage], candidates: list[CandidateIdentity]
    ) -> list[Lead]:
        leads: dict[tuple[str, str], Lead] = {}
        page_to_candidate = {pid: c.id for c in candidates for pid in c.page_ids}

        def add(lead: Lead) -> None:
            key = (lead.kind.value, lead.value)
            existing = leads.get(key)
            if existing is None:
                leads[key] = lead
            else:
                existing.candidate_ids = sorted(set(existing.candidate_ids) | set(lead.candidate_ids))

        for email in hints.emails:
            add(
                Lead(
                    kind=LeadKind.EMAIL,
                    value=email,
                    status=LeadStatus.PROVIDED,
                    classification="PROVIDED_EMAIL",
                    note="Supplied by the investigator.",
                )
            )
        for page in pages:
            cid = page_to_candidate.get(page.id)
            for email in page.profile.emails:
                add(
                    Lead(
                        kind=LeadKind.EMAIL,
                        value=email,
                        status=LeadStatus.OBSERVED,
                        classification="OBSERVED_EMAIL",
                        source_url=page.url,
                        candidate_ids=[cid] if cid else [],
                        note="Seen on a candidate source (the address may belong to someone else on that page).",
                    )
                )
            for link in page.profile.social_links:
                domain = registrable_domain(link)
                if domain and domain not in SOCIAL_DOMAINS and platform_for(link) is None:
                    add(
                        Lead(
                            kind=LeadKind.DOMAIN,
                            value=domain,
                            status=LeadStatus.OBSERVED,
                            classification="LINKED_PERSONAL_DOMAIN",
                            source_url=page.url,
                            candidate_ids=[cid] if cid else [],
                            note="Linked from a candidate profile; ownership is not verified.",
                        )
                    )

        if self.settings.generate_email_candidates:
            for email in generate_email_candidates(hints.name):
                if (LeadKind.EMAIL.value, email) not in leads:
                    add(
                        Lead(
                            kind=LeadKind.EMAIL,
                            value=email,
                            status=LeadStatus.GENERATED_CANDIDATE,
                            classification="GENERATED_EMAIL_CANDIDATE",
                            note="Hypothesis generated from the name pattern — NOT a discovery.",
                        )
                    )
        if self.settings.generate_domain_candidates:
            for domain in generate_domain_candidates(hints.name):
                if await self._domain_resolves(domain):
                    add(
                        Lead(
                            kind=LeadKind.DOMAIN,
                            value=domain,
                            status=LeadStatus.GENERATED_CANDIDATE,
                            classification="DOMAIN_EXISTS_UNVERIFIED",
                            note="The domain exists. Nothing links it to this person unless a candidate profile does.",
                        )
                    )

        email_leads = [lead for lead in leads.values() if lead.kind == LeadKind.EMAIL]
        if self.settings.hibp_api_key:
            await asyncio.gather(*(self._hibp(lead) for lead in email_leads[:10]))
        if self.settings.smtp_verification_enabled:
            for lead in email_leads[:6]:
                if await asyncio.to_thread(self._smtp_accepts, lead.value):
                    lead.note = (
                        lead.note or ""
                    ) + " SMTP server accepted the recipient (servers often accept any address)."
                    if lead.status == LeadStatus.GENERATED_CANDIDATE:
                        lead.status, lead.classification = LeadStatus.VERIFIED, "VERIFIED_EMAIL"
        return list(leads.values())

    async def _domain_resolves(self, domain: str) -> bool:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        except OSError:
            return False
        return any(is_ip_allowed(str(info[4][0])) for info in infos)

    async def _hibp(self, lead: Lead) -> None:
        if not self.settings.hibp_api_key:
            return
        try:
            resp = await self.client.get(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(lead.value)}",
                headers={
                    "hibp-api-key": self.settings.hibp_api_key.get_secret_value(),
                    "user-agent": "PersonIntelAgent",
                },
                params={"truncateResponse": "true"},
            )
        except httpx.HTTPError:
            return
        if resp.status_code == 200:
            try:
                breaches = [b.get("Name") for b in resp.json() if isinstance(b, dict)]
            except ValueError:
                return
            lead.note = (lead.note or "") + f" Appears in {len(breaches)} breach(es) (address existed)."
            if lead.status == LeadStatus.GENERATED_CANDIDATE:
                lead.status, lead.classification = LeadStatus.VERIFIED, "VERIFIED_EMAIL"

    @staticmethod
    def _smtp_accepts(email: str, timeout: int = 5) -> bool:
        """Opt-in SMTP RCPT probe (disabled by default; no mail is sent)."""
        try:
            import dns.resolver
        except ImportError:
            return False
        if not normalize_email(email):
            return False
        domain = email.split("@", 1)[1]
        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
            host = str(min(answers, key=lambda r: r.preference).exchange).rstrip(".")
            with smtplib.SMTP(host, 25, timeout=timeout) as smtp:
                smtp.ehlo_or_helo_if_needed()
                smtp.mail("verify@invalid.example")
                code, _ = smtp.rcpt(email)
                return code in (250, 251)
        except Exception:
            return False
