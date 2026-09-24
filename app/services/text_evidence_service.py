"""Identity/text evidence: compare the supplied hints with what a page says.

Only hints the investigator supplied are checked — without hints there is no
text evidence (we never invent identity claims).
"""

from __future__ import annotations

import re

from app.analysis.location import expand_location
from app.domain.candidate import CandidatePage
from app.domain.evidence import Evidence, EvidenceStrength, EvidenceType, TextMatch
from app.domain.identity import IdentityHints
from app.services.evidence_builder import make_evidence
from app.utils.canonical import (
    canonical_url,
    name_tokens,
    normalize_email,
    normalize_name,
    normalize_text,
    normalize_username,
)


def _excerpt(corpus: str, needle: str, width: int = 60) -> str:
    idx = corpus.find(needle)
    if idx < 0:
        return needle
    start, end = max(0, idx - width), min(len(corpus), idx + len(needle) + width)
    return ("…" if start else "") + corpus[start:end].strip() + ("…" if end < len(corpus) else "")


def _phrase_in(corpus: str, phrase: str) -> bool:
    if not phrase:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", corpus) is not None


def page_corpus(page: CandidatePage) -> str:
    p = page.profile
    parts = [
        page.title,
        page.snippet,
        p.title,
        p.description,
        p.profile_name,
        p.site_name,
        p.text_excerpt,
        " ".join(p.locations),
        " ".join(p.organizations),
    ]
    return normalize_text(" \n ".join(x for x in parts if x))


class TextEvidenceService:
    def evaluate(self, page: CandidatePage, hints: IdentityHints) -> list[Evidence]:
        if hints.is_empty():
            return []
        corpus = page_corpus(page)
        name_corpus = normalize_name(corpus)
        url = page.url
        out: list[Evidence] = []

        def add(
            type_: EvidenceType,
            strength: EvidenceStrength,
            field: str,
            expected: str,
            observed: str,
            text: str,
            exact: bool = True,
        ) -> None:
            out.append(
                make_evidence(
                    type_,
                    strength,
                    text,
                    url,
                    text=TextMatch(field=field, expected=expected, observed=observed[:200], exact=exact),
                )
            )

        # --- name --------------------------------------------------------------------
        if hints.name:
            full = normalize_name(hints.name)
            tokens = name_tokens(hints.name)
            profile_name = normalize_name(page.profile.profile_name)
            if len(tokens) >= 2 and (profile_name == full or _phrase_in(name_corpus, full)):
                observed = page.profile.profile_name if profile_name == full else _excerpt(name_corpus, full)
                where = "profile name" if profile_name == full else "page text"
                add(
                    EvidenceType.NAME_MATCH,
                    EvidenceStrength.MODERATE,
                    "name",
                    hints.name,
                    observed or full,
                    f"The {where} contains the full name “{hints.name}”. (Names are not unique.)",
                )
            elif len(tokens) >= 2 and _phrase_in(name_corpus, tokens[0]) and _phrase_in(name_corpus, tokens[-1]):
                add(
                    EvidenceType.NAME_MATCH,
                    EvidenceStrength.WEAK,
                    "name",
                    hints.name,
                    f"{tokens[0]} … {tokens[-1]}",
                    f"The page mentions “{tokens[0]}” and “{tokens[-1]}” but not the full name together.",
                    exact=False,
                )

        # --- identifiers --------------------------------------------------------------
        page_usernames = {normalize_username(u) for u in page.profile.usernames}
        for username in hints.usernames:
            u = normalize_username(username)
            if u in page_usernames:
                add(
                    EvidenceType.USERNAME_MATCH,
                    EvidenceStrength.STRONG,
                    "username",
                    username,
                    u,
                    f"The account handle on this profile is “{u}”, identical to a supplied username.",
                )
            elif len(u) >= 5 and _phrase_in(corpus, u):
                add(
                    EvidenceType.USERNAME_MATCH,
                    EvidenceStrength.MODERATE,
                    "username",
                    username,
                    _excerpt(corpus, u),
                    f"The page mentions the supplied username “{u}”.",
                    exact=False,
                )
        page_emails = {normalize_email(e) for e in page.profile.emails}
        for email in hints.emails:
            e = normalize_email(email)
            if e and (e in page_emails or email.lower() in corpus):
                add(
                    EvidenceType.EMAIL_MATCH,
                    EvidenceStrength.STRONG,
                    "email",
                    email,
                    email,
                    f"The supplied email address {email} appears on this source.",
                )
        known = {canonical_url(u) for u in hints.known_urls}
        if canonical_url(url) in known or (page.profile.canonical_url and page.profile.canonical_url in known):
            add(
                EvidenceType.KNOWN_URL,
                EvidenceStrength.STRONG,
                "known_url",
                url,
                url,
                "This is one of the profile/website URLs supplied by the investigator.",
            )

        # --- context attributes ---------------------------------------------------------
        if hints.location:
            expansion = expand_location(hints.location)
            city = normalize_text(hints.location)
            if _phrase_in(corpus, city):
                add(
                    EvidenceType.LOCATION_MATCH,
                    EvidenceStrength.MODERATE,
                    "location",
                    hints.location,
                    _excerpt(corpus, city),
                    f"The source mentions the location “{hints.location}”.",
                )
            else:
                for term in expansion.search_terms[1:]:
                    t = normalize_text(term)
                    if len(t) >= 3 and _phrase_in(corpus, t):
                        add(
                            EvidenceType.LOCATION_MATCH,
                            EvidenceStrength.WEAK,
                            "location",
                            hints.location,
                            _excerpt(corpus, t),
                            f"The source mentions “{term}”, a broader area containing “{hints.location}”.",
                            exact=False,
                        )
                        break
        if hints.country and not any(e.type == EvidenceType.LOCATION_MATCH for e in out):
            c = normalize_text(hints.country)
            if len(c) >= 3 and _phrase_in(corpus, c):
                add(
                    EvidenceType.LOCATION_MATCH,
                    EvidenceStrength.WEAK,
                    "country",
                    hints.country,
                    _excerpt(corpus, c),
                    f"The source mentions the country “{hints.country}”.",
                    exact=False,
                )
        for value, type_, field, strength in (
            (hints.employer, EvidenceType.EMPLOYER_MATCH, "employer", EvidenceStrength.MODERATE),
            (hints.university, EvidenceType.EDUCATION_MATCH, "university", EvidenceStrength.MODERATE),
            (hints.profession, EvidenceType.PROFESSION_MATCH, "profession", EvidenceStrength.WEAK),
        ):
            if value:
                v = normalize_text(value)
                if len(v) >= 3 and _phrase_in(corpus, v):
                    add(type_, strength, field, value, _excerpt(corpus, v), f"The source mentions “{value}” ({field}).")
        return out
