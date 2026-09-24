from app.domain.candidate import CandidatePage, PageProfile
from app.domain.evidence import EvidenceStrength, EvidenceType
from app.domain.identity import IdentityHints
from app.domain.image import ImageOrigin
from app.services.lead_service import generate_email_candidates
from app.services.page_analysis_service import parse_html
from app.services.text_evidence_service import TextEvidenceService

HTML = """<html><head><title>Jane Doe | Engineer</title>
<link rel="canonical" href="/people/jane/">
<meta property="og:image" content="https://cdn.example.org/og.jpg">
<meta name="description" content="Jane Doe is a software engineer at Example GmbH in Vienna.">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Person","name":"Jane Doe",
 "image":"/img/jane.jpg","address":{"@type":"PostalAddress","addressLocality":"Vienna"},
 "worksFor":{"@type":"Organization","name":"Example GmbH"},"sameAs":["https://github.com/janedoe93"]}</script>
<script>var x = "<img src='/evil.jpg'>";</script>
</head><body>
<img src="/static/logo.png" alt="logo"><img src="/icons/star.svg">
<img src="/uploads/avatar-jane.jpg" alt="Jane Doe" width="200" height="200">
<a href="mailto:jane.doe@example.org">mail</a> <a href="https://x.com/janedoe93">X</a>
<p>Contact: info@example.org</p></body></html>"""


def test_parse_html_extracts_profile_and_prioritises_images():
    profile, images = parse_html(HTML, "https://example.org/people/jane", max_images=3)
    assert profile.profile_name == "Jane Doe"
    assert profile.canonical_url == "https://example.org/people/jane"
    assert "Vienna" in profile.locations and "Example GmbH" in profile.organizations
    assert "jane.doe@example.org" in profile.emails and "info@example.org" not in profile.emails
    assert any("github.com/janedoe93" in u for u in profile.social_links)
    assert any("x.com/janedoe93" in u for u in profile.social_links)
    urls = [i.url for i in images]
    assert len(images) == 3
    assert "https://example.org/img/jane.jpg" in urls and "https://cdn.example.org/og.jpg" in urls
    assert not any("logo" in u or u.endswith(".svg") or "evil" in u for u in urls)
    assert images[0].origin in (ImageOrigin.STRUCTURED_DATA, ImageOrigin.OPEN_GRAPH)


def page(text: str, **profile) -> CandidatePage:
    return CandidatePage(id="p", url="https://example.org/p", canonical_url="https://example.org/p", domain="example.org",
                         profile=PageProfile(text_excerpt=text, **profile))


def test_text_evidence_matches_supplied_hints_only():
    svc = TextEvidenceService()
    assert svc.evaluate(page("Jane Doe from Vienna"), IdentityHints()) == []
    ev = svc.evaluate(page("Jane Doe works at Example GmbH in Wien, Austria", usernames=["janedoe93"],
                           emails=["jane@example.org"]),
                      IdentityHints(name="Jane Doe", location="Vienna", employer="Example GmbH",
                                    usernames=["janedoe93"], emails=["jane@example.org"]))
    types = {e.type: e for e in ev}
    assert types[EvidenceType.NAME_MATCH].strength == EvidenceStrength.MODERATE
    assert types[EvidenceType.USERNAME_MATCH].strength == EvidenceStrength.STRONG
    assert types[EvidenceType.EMAIL_MATCH].strength == EvidenceStrength.STRONG
    assert types[EvidenceType.EMPLOYER_MATCH].strength == EvidenceStrength.MODERATE
    # "Vienna" is not literally present, but "Wien"/"Austria" (expansions) are → weak location match
    assert types[EvidenceType.LOCATION_MATCH].strength == EvidenceStrength.WEAK


def test_partial_name_is_weak_and_word_boundaries_respected():
    svc = TextEvidenceService()
    ev = svc.evaluate(page("Jane Smith and John Doe"), IdentityHints(name="Jane Doe"))
    assert ev[0].strength == EvidenceStrength.WEAK and not ev[0].text.exact
    assert svc.evaluate(page("Janet Doerr"), IdentityHints(name="Jane Doe")) == []


def test_generated_emails_are_only_hypotheses():
    emails = generate_email_candidates("Jane Doe")
    assert "jane.doe@gmail.com" in emails and len(emails) <= 15
    assert generate_email_candidates("Cher") == []


async def test_leads_disabled_by_default_and_labelled(make_container):
    from app.domain.investigation import LeadStatus

    c = make_container()
    leads = await c.investigations.leads.collect(IdentityHints(name="Jane Doe", emails=["jane@example.org"]),
                                                 [page("x", emails=["jd@example.net"])], [])
    kinds = {lead.classification for lead in leads}
    assert kinds == {"PROVIDED_EMAIL", "OBSERVED_EMAIL"}
    assert all(lead.status != LeadStatus.GENERATED_CANDIDATE for lead in leads)
