import numpy as np

from app.domain.candidate import CandidateIdentity, CandidatePage, EvidenceLevel, PageProfile
from app.domain.evidence import EvidenceStrength, EvidenceType, FaceEvidence, FaceMatchBand, TextMatch
from app.domain.identity import IdentityHints
from app.domain.image import ImageDiscoveryResult, ImageMatchType
from app.services.candidate_clustering_service import CandidateClusteringService, PageNode
from app.services.evidence_builder import face_evidence, image_occurrence_evidence, make_evidence
from app.services.evidence_fusion_service import EvidenceFusionService
from app.services.face_matching_service import FaceMatchingService
from tests.conftest import make_settings

URL = "https://example.org/p"


def face(band: FaceMatchBand, distance: float) -> FaceEvidence:
    return FaceEvidence(model="ArcFace", distance=distance, cosine_similarity=1 - distance, match_band=band,
                        reference_image_id="r", reference_face_id="f1", candidate_image_url=URL + "/i.jpg",
                        candidate_face_id="c1")


def text(type_: EvidenceType, strength=EvidenceStrength.MODERATE, field="name", exact=True):
    return make_evidence(type_, strength, "obs", URL, text=TextMatch(field=field, expected="x", observed="x", exact=exact))


def exact_image():
    return image_occurrence_evidence(ImageDiscoveryResult(provider="p", match_type=ImageMatchType.EXACT,
                                                          image_url=URL + "/r.jpg", page_url=URL), URL)


def assess(evidence, best_face=None, hints=None):
    cand = CandidateIdentity(id="c", display_name="x", urls=[URL], evidence=evidence, best_face=best_face)
    return EvidenceFusionService().assess(cand, hints or IdentityHints(name="Jane Doe", location="Vienna"), True)


def test_levels():
    assert assess([]).level == EvidenceLevel.INSUFFICIENT
    assert assess([text(EvidenceType.NAME_MATCH)]).level == EvidenceLevel.WEAK
    assert assess([exact_image()]).level == EvidenceLevel.MODERATE
    f = face(FaceMatchBand.MEDIUM, 0.6)
    assert assess([face_evidence(f, URL)], best_face=f).level == EvidenceLevel.WEAK
    f = face(FaceMatchBand.HIGH, 0.5)
    assert assess([face_evidence(f, URL)], best_face=f).level == EvidenceLevel.WEAK
    ctx = [text(EvidenceType.NAME_MATCH), text(EvidenceType.LOCATION_MATCH, field="location")]
    assert assess(ctx + [face_evidence(f, URL)], best_face=f).level == EvidenceLevel.STRONG
    f_vh = face(FaceMatchBand.VERY_HIGH, 0.2)
    assert assess([exact_image(), face_evidence(f_vh, URL)] + ctx, best_face=f_vh).level == EvidenceLevel.VERY_STRONG


def test_contradicting_face_caps_level():
    f = face(FaceMatchBand.NO_MATCH, 0.95)
    ctx = [text(EvidenceType.NAME_MATCH), text(EvidenceType.LOCATION_MATCH, field="location"),
           text(EvidenceType.EMPLOYER_MATCH, field="employer")]
    a = assess(ctx + [face_evidence(f, URL)], best_face=f)
    assert a.level == EvidenceLevel.WEAK
    assert any("do NOT look alike" in c for c in a.caveats)


def test_match_states():
    a = assess([text(EvidenceType.NAME_MATCH)], hints=IdentityHints(name="Jane Doe", usernames=["jd"]))
    assert a.name_match.value == "YES"
    assert a.username_match.value == "NO"
    assert a.location_match.value == "UNKNOWN"


def _node(url: str, *, name: str | None = None, usernames=(), links=(), emb=None, evidence=None) -> PageNode:
    page = CandidatePage(id=url[-6:], url=url, canonical_url=url, domain="d",
                         profile=PageProfile(profile_name=name, usernames=list(usernames), outbound_links=list(links)))
    ev = evidence if evidence is not None else [make_evidence(EvidenceType.NAME_MATCH, EvidenceStrength.MODERATE, "n", url,
                                                              text=TextMatch(field="name", expected="Jane Doe", observed="Jane Doe"))]
    return PageNode(page=page, evidence=ev, best_face_embedding=emb)


def clustering(tmp_path):
    return CandidateClusteringService(FaceMatchingService(make_settings(tmp_path), backend=None))


def test_same_common_name_does_not_merge(tmp_path):
    out = clustering(tmp_path).cluster([_node("https://a.example.com/1", name="Jane Doe"),
                                        _node("https://b.example.com/2", name="Jane Doe")])
    assert len(out) == 2


def test_name_plus_handle_merges(tmp_path):
    out = clustering(tmp_path).cluster([_node("https://github.com/janedoe93", name="Jane Doe", usernames=["janedoe93"]),
                                        _node("https://x.com/janedoe93", name="Jane Doe", usernames=["janedoe93"])])
    assert len(out) == 1 and out[0].cluster_reasons


def test_explicit_cross_link_merges_and_creates_evidence(tmp_path):
    out = clustering(tmp_path).cluster([_node("https://a.example.com/me", links=["https://b.example.net/jane"]),
                                        _node("https://b.example.net/jane")])
    assert len(out) == 1
    assert any(e.type == EvidenceType.CROSS_LINK for e in out[0].evidence)


def test_face_similarity_alone_does_not_merge(tmp_path):
    e = np.array([1.0, 0.0, 0.0])
    out = clustering(tmp_path).cluster([_node("https://a.example.com/1", emb=e), _node("https://b.example.com/2", emb=e)])
    assert len(out) == 2


def test_irrelevant_pages_are_not_candidates(tmp_path):
    f = face(FaceMatchBand.NO_MATCH, 0.9)
    out = clustering(tmp_path).cluster([_node("https://a.example.com/1", evidence=[face_evidence(f, "https://a.example.com/1")])])
    assert out == []


def test_contradictory_faces_veto_moderate_merge(tmp_path):
    loc = [make_evidence(EvidenceType.LOCATION_MATCH, EvidenceStrength.MODERATE, "l", "u",
                         text=TextMatch(field="location", expected="Vienna", observed="Vienna"))]
    e = np.array([1.0, 0.0, 0.0])
    a = _node("https://a.example.com/1", name="Jane Doe", usernames=["janedoe"], emb=e, evidence=loc)
    b = _node("https://b.example.com/2", name="Jane Doe", usernames=["janedoe"], emb=e, evidence=loc)
    a.best_face = face(FaceMatchBand.VERY_HIGH, 0.2)
    b.best_face = face(FaceMatchBand.NO_MATCH, 0.95)
    assert len(clustering(tmp_path).cluster([a, b])) == 2
