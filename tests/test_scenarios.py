"""End-to-end investigation scenarios with mocked providers and synthetic imagery."""

from __future__ import annotations

import pytest

from app.domain.candidate import EvidenceLevel
from app.domain.evidence import EvidenceType, FaceMatchBand
from app.domain.identity import IdentityHints
from app.domain.image import ImageDiscoveryResult, ImageMatchType
from app.domain.investigation import EventType, InvestigationStatus, ProviderOutcome
from tests.fakes import (
    JANE,
    JANE_OTHER,
    LOOKALIKE,
    LOOKALIKE_VH,
    OTHER,
    ExplodingProvider,
    FakeReverseProvider,
    FakeSearchProvider,
    crop_image,
    make_image,
)

REFERENCE = make_image([(JANE, (100, 80, 110))], seed=1)
JANE_SECOND_PHOTO = make_image([(JANE_OTHER, (60, 60, 120))], seed=2)


def profile_html(name: str, *, location: str = "", image: str = "", extra: str = "", title: str | None = None) -> str:
    return f"""<html><head><title>{title or name} | Example</title>
    <meta property="og:title" content="{name}">
    {f'<meta property="og:image" content="{image}">' if image else ''}
    </head><body><h1>{name}</h1><p>{location}</p>{extra}</body></html>"""


async def run(container, hints: IdentityHints | None = None, images: list[bytes] | None = None):
    events = []
    inv = container.investigations.create(hints or IdentityHints())
    for i, data in enumerate(images or []):
        await container.investigations.add_reference_image(inv.id, f"ref{i}.png", data)
    result = await container.investigations.investigate(inv.id, progress=lambda t, m, d: events.append(t))
    return result, events


def by_url(result, fragment: str):
    for cand in result.candidates:
        if any(fragment in u for u in cand.urls):
            return cand
    raise AssertionError(f"no candidate with {fragment}: {[c.urls for c in result.candidates]}")


def types(candidate) -> set[EvidenceType]:
    return {e.type for e in candidate.evidence}


# --------------------------------------------------------------------- Scenario A
async def test_scenario_a_exact_image(make_container, world):
    world.page("https://example.org/team/jane", profile_html("Jane Doe", image="https://cdn.example.org/jane.png"))
    world.image("https://cdn.example.org/jane.png", REFERENCE)
    provider = FakeReverseProvider([ImageDiscoveryResult(
        provider="fake_reverse", match_type=ImageMatchType.EXACT,
        image_url="https://cdn.example.org/jane.png", page_url="https://example.org/team/jane")])
    c = make_container(reverse_providers=[provider])
    result, events = await run(c, images=[REFERENCE])

    assert result.status == InvestigationStatus.COMPLETED
    assert any(p.url == "https://example.org/team/jane" for p in result.pages)
    cand = by_url(result, "example.org/team/jane")
    assert EvidenceType.EXACT_IMAGE in types(cand)
    # The copy of the reference photo must not ALSO count as independent face evidence.
    assert EvidenceType.FACE_SIMILARITY not in types(cand)
    assert cand.assessment.level == EvidenceLevel.MODERATE
    for expected in (EventType.IMAGE_VALIDATED, EventType.FACE_DETECTED, EventType.REFERENCE_EMBEDDING_CREATED,
                     EventType.REVERSE_IMAGE_SEARCH_STARTED, EventType.REVERSE_IMAGE_RESULT_FOUND,
                     EventType.CANDIDATE_PAGE_DISCOVERED, EventType.CANDIDATE_IMAGE_DOWNLOADED,
                     EventType.CANDIDATE_UPDATED, EventType.INVESTIGATION_COMPLETED):
        assert expected in events, expected


async def test_exact_image_detected_locally_by_phash_without_provider_flag(make_container, world):
    """A page found by text search that embeds the reference photo gets EXACT_IMAGE via pHash."""
    world.page("https://blog.example.net/about", profile_html("Jane Doe", image="https://blog.example.net/me.jpg"))
    world.image("https://blog.example.net/me.jpg", REFERENCE)
    search = FakeSearchProvider({'"Jane Doe"': [("https://blog.example.net/about", "About Jane Doe", "")]})
    c = make_container(search_providers=[search])
    result, _ = await run(c, IdentityHints(name="Jane Doe"), [REFERENCE])
    cand = by_url(result, "blog.example.net")
    exact = [e for e in cand.evidence if e.type == EvidenceType.EXACT_IMAGE]
    assert exact and exact[0].image.provider == "local_phash"


# --------------------------------------------------------------------- Scenario B
async def test_scenario_b_modified_image(make_container, world):
    cropped = crop_image(REFERENCE, (40, 30, 300, 300))
    world.page("https://news.example.com/story", profile_html("Community event", image="https://news.example.com/crop.png"))
    world.image("https://news.example.com/crop.png", cropped)
    provider = FakeReverseProvider([ImageDiscoveryResult(
        provider="fake_reverse", match_type=ImageMatchType.PARTIAL,
        image_url="https://news.example.com/crop.png", page_url="https://news.example.com/story")])
    c = make_container(reverse_providers=[provider])
    result, _ = await run(c, images=[REFERENCE])
    cand = by_url(result, "news.example.com")
    assert EvidenceType.PARTIAL_IMAGE in types(cand)
    assert cand.assessment.image_occurrence == ImageMatchType.PARTIAL


# --------------------------------------------------------------------- Scenario C
async def test_scenario_c_different_photo_same_identity(make_container, world):
    world.page("https://social.example/janedoe93", profile_html(
        "Jane Doe", location="Software engineer in Vienna", image="https://social.example/avatars/janedoe93.png"))
    world.image("https://social.example/avatars/janedoe93.png", JANE_SECOND_PHOTO)
    search = FakeSearchProvider({'"Jane Doe"': [("https://social.example/janedoe93", "Jane Doe", "Vienna")]})
    c = make_container(search_providers=[search])
    result, events = await run(c, IdentityHints(name="Jane Doe", location="Vienna"), [REFERENCE])

    cand = by_url(result, "social.example/janedoe93")
    t = types(cand)
    assert EvidenceType.FACE_SIMILARITY in t
    assert EvidenceType.NAME_MATCH in t and EvidenceType.LOCATION_MATCH in t
    assert cand.best_face is not None and cand.best_face.match_band == FaceMatchBand.VERY_HIGH
    assert cand.assessment.level == EvidenceLevel.STRONG
    assert EventType.FACE_MATCH_FOUND in events and EventType.TEXT_EVIDENCE_FOUND in events
    # Never expressed as a probability
    assert "%" not in " ".join(cand.assessment.reasons)


async def test_very_strong_requires_multiple_independent_signals(make_container, world):
    world.page("https://uni.example.edu/people/jdoe", profile_html(
        "Jane Doe", location="Vienna",
        extra='<img src="/img/portrait.png" alt="Jane Doe portrait" width="300"><img src="/img/team.png" width="400">'))
    world.image("https://uni.example.edu/img/portrait.png", REFERENCE)
    world.image("https://uni.example.edu/img/team.png", JANE_SECOND_PHOTO)
    provider = FakeReverseProvider([ImageDiscoveryResult(
        provider="fake_reverse", match_type=ImageMatchType.EXACT,
        image_url="https://uni.example.edu/img/portrait.png", page_url="https://uni.example.edu/people/jdoe")])
    c = make_container(reverse_providers=[provider])
    result, _ = await run(c, IdentityHints(name="Jane Doe", location="Vienna"), [REFERENCE])
    cand = by_url(result, "uni.example.edu")
    assert {"IMAGE", "FACE", "CONTEXT"} <= set(cand.assessment.strong_signals)
    assert cand.assessment.level == EvidenceLevel.VERY_STRONG


# --------------------------------------------------------------------- Scenario D
async def test_scenario_d_wrong_person_same_name(make_container, world):
    other_photo = make_image([(OTHER, (80, 80, 120))], seed=7)
    world.page("https://social.example/jane.doe.other", profile_html(
        "Jane Doe", location="Vienna", image="https://social.example/avatars/other.png"))
    world.image("https://social.example/avatars/other.png", other_photo)
    world.page("https://directory.example.org/jane-doe", profile_html("Jane Doe", location="Vienna, Austria"))
    search = FakeSearchProvider({'"Jane Doe"': [
        ("https://social.example/jane.doe.other", "Jane Doe", ""),
        ("https://directory.example.org/jane-doe", "Jane Doe - Vienna", ""),
    ]})
    c = make_container(search_providers=[search])
    result, _ = await run(c, IdentityHints(name="Jane Doe", location="Vienna"), [REFERENCE])

    wrong = by_url(result, "jane.doe.other")
    assert wrong.best_face is not None and wrong.best_face.match_band == FaceMatchBand.NO_MATCH
    assert wrong.assessment.level.rank <= EvidenceLevel.WEAK.rank
    directory = by_url(result, "directory.example.org")
    assert directory.assessment.level.rank < EvidenceLevel.STRONG.rank
    # Same common name must not merge the two pages into one identity.
    assert wrong.id != directory.id


async def test_name_only_never_strong(make_container, world):
    world.page("https://a.example.com/p", profile_html("Jane Doe"))
    search = FakeSearchProvider({'"Jane Doe"': [("https://a.example.com/p", "Jane Doe", "")]})
    c = make_container(search_providers=[search])
    result, _ = await run(c, IdentityHints(name="Jane Doe"))
    cand = by_url(result, "a.example.com")
    assert cand.assessment.level == EvidenceLevel.WEAK


# --------------------------------------------------------------------- Scenario E
@pytest.mark.parametrize("colour,max_level", [(LOOKALIKE, EvidenceLevel.WEAK), (LOOKALIKE_VH, EvidenceLevel.MODERATE)])
async def test_scenario_e_visually_similar_wrong_person(make_container, world, colour, max_level):
    lookalike = make_image([(colour, (50, 50, 140))], seed=9)
    world.image("https://images.example.com/similar.png", lookalike)
    provider = FakeReverseProvider([ImageDiscoveryResult(
        provider="fake_reverse", match_type=ImageMatchType.VISUALLY_SIMILAR, image_url="https://images.example.com/similar.png")])
    c = make_container(reverse_providers=[provider])
    result, _ = await run(c, images=[REFERENCE])
    cand = by_url(result, "images.example.com")
    assert EvidenceType.FACE_SIMILARITY in types(cand)
    assert cand.assessment.level.rank <= max_level.rank
    assert cand.assessment.level.rank < EvidenceLevel.STRONG.rank


# --------------------------------------------------------------------- Scenario F
async def test_scenario_f_no_match(make_container, world):
    c = make_container(reverse_providers=[FakeReverseProvider([])], search_providers=[FakeSearchProvider({})])
    result, events = await run(c, IdentityHints(name="Nobody Known"), [REFERENCE])
    assert result.candidates == []
    assert result.conclusion.startswith("No strong candidate found")
    assert result.status == InvestigationStatus.COMPLETED
    assert EventType.INVESTIGATION_COMPLETED in events


# ------------------------------------------------------------ resilience / misc
async def test_provider_failures_do_not_fail_investigation(make_container, world):
    world.page("https://social.example/janedoe93", profile_html("Jane Doe", image="https://social.example/a.png"))
    world.image("https://social.example/a.png", JANE_SECOND_PHOTO)
    good = FakeSearchProvider({'"Jane Doe"': [("https://social.example/janedoe93", "Jane Doe", "")]})
    broken_search = FakeSearchProvider(error=RuntimeError("malformed payload"))
    c = make_container(reverse_providers=[ExplodingProvider(), FakeReverseProvider(configured=False)],
                       search_providers=[broken_search, good])
    result, _ = await run(c, IdentityHints(name="Jane Doe"), [REFERENCE])
    outcomes = {(r.provider, r.outcome) for r in result.provider_runs}
    assert ("fake_reverse", ProviderOutcome.FAILED) in outcomes
    assert ("fake_reverse", ProviderOutcome.NOT_CONFIGURED) in outcomes
    assert ("fake_search", ProviderOutcome.FAILED) in outcomes
    assert result.status == InvestigationStatus.COMPLETED
    assert by_url(result, "social.example/janedoe93")  # the working provider still produced a candidate


async def test_image_only_search_works_without_hints(make_container, world):
    world.page("https://example.org/p", profile_html("Someone", image="https://example.org/i.png"))
    world.image("https://example.org/i.png", REFERENCE)
    provider = FakeReverseProvider([ImageDiscoveryResult(provider="fake_reverse", match_type=ImageMatchType.EXACT,
                                                         image_url="https://example.org/i.png", page_url="https://example.org/p")])
    c = make_container(reverse_providers=[provider])
    result, _ = await run(c, None, [REFERENCE])
    assert result.candidates and result.candidates[0].assessment.name_match.value == "UNKNOWN"


async def test_multiple_faces_user_selects_target(make_container, world):
    group = make_image([(OTHER, (10, 10, 120)), (JANE, (170, 150, 100))], seed=5)
    c = make_container()
    inv = c.investigations.create(IdentityHints(name="Jane Doe"))
    ref = await c.investigations.add_reference_image(inv.id, "group.png", group)
    assert len(ref.faces) == 2
    jane_face = next(f for f in ref.faces if f.bbox.x == 170)
    updated = c.reference_images.select_face(inv.id, ref.id, jane_face.id)
    assert updated.selected_face_id == jane_face.id
    stored = c.repo.get_reference_images(inv.id, with_embeddings=True)[0]
    assert stored.selected_face.embedding is not None  # selection keeps embeddings


async def test_cross_linked_profiles_are_clustered(make_container, world):
    world.page("https://code.example.com/janedoe93", profile_html(
        "Jane Doe", image="https://code.example.com/av.png",
        extra='<a href="https://janedoe.example/">website</a>'))
    world.image("https://code.example.com/av.png", JANE_SECOND_PHOTO)
    world.page("https://janedoe.example/", profile_html("Jane Doe — personal site", extra="<p>Vienna</p>",
                                                        title="Jane Doe"))
    search = FakeSearchProvider({'"Jane Doe"': [("https://code.example.com/janedoe93", "Jane Doe", ""),
                                                ("https://janedoe.example/", "Jane Doe", "")]})
    c = make_container(search_providers=[search])
    result, _ = await run(c, IdentityHints(name="Jane Doe", location="Vienna"), [REFERENCE])
    merged = by_url(result, "code.example.com")
    assert any("janedoe.example" in u for u in merged.urls)
    assert EvidenceType.CROSS_LINK in types(merged)
    assert merged.cluster_reasons
