"""Group candidate pages that plausibly describe the same person.

Merge rules (conservative — ambiguous pages stay separate):

* STRONG link (any one is enough): explicit cross-link between the pages,
  the same observed email address, a profile linking to the other page's
  personal domain.
* MODERATE links: same account handle, same full profile name, same avatar
  photo (pHash), HIGH+ face similarity between their best faces, same
  organisation, same location.
* VETO (moderate links ignored): one page's face matches the target (HIGH+)
  while the other's clearly does not (LOW / NO_MATCH).

Two pages merge on a strong link, or on ≥2 moderate links of which at least
one is non-textual (handle / avatar / face), or on ≥3 moderate links. A shared
common name — or a single mediocre face score — never merges anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from app.domain.candidate import CandidateIdentity, CandidatePage
from app.domain.evidence import Evidence, EvidenceStrength, EvidenceType, FaceEvidence, FaceMatchBand
from app.services.evidence_builder import make_evidence
from app.services.face_matching_service import FaceMatchingService
from app.services.page_analysis_service import clean_title
from app.utils.canonical import (
    canonical_url,
    dedupe_preserve_order,
    domain_of,
    name_tokens,
    normalize_email,
    normalize_name,
    normalize_text,
    registrable_domain,
    stable_hash,
)
from app.utils.platforms import SOCIAL_DOMAINS, platform_for, source_quality
from app.vision.image_hash import hamming_distance

_NON_TEXTUAL = {"username", "avatar", "face"}


@dataclass
class PageNode:
    page: CandidatePage
    evidence: list[Evidence]
    best_face: FaceEvidence | None = None
    best_face_embedding: np.ndarray | None = None

    @property
    def relevant(self) -> bool:
        for ev in self.evidence:
            if ev.type == EvidenceType.FACE_SIMILARITY:
                if ev.face and ev.face.match_band.rank >= FaceMatchBand.MEDIUM.rank:
                    return True
            elif ev.type != EvidenceType.SIMILAR_IMAGE:
                return True
        return False


@dataclass
class Link:
    strong: list[str] = field(default_factory=list)
    moderate: list[tuple[str, str]] = field(default_factory=list)  # (kind, description)
    veto: str | None = None

    def should_merge(self) -> bool:
        if self.strong:
            return True
        if self.veto:
            return False
        kinds = {k for k, _ in self.moderate}
        return (len(kinds) >= 2 and bool(kinds & _NON_TEXTUAL)) or len(kinds) >= 3


def _label(page: CandidatePage) -> str:
    return domain_of(page.url) + (f" ({page.platform})" if page.platform else "")


class CandidateClusteringService:
    def __init__(self, faces: FaceMatchingService):
        self.faces = faces

    # ------------------------------------------------------------------ links
    def link(self, a: PageNode, b: PageNode) -> Link:
        link = Link()
        if a.best_face and b.best_face:
            bands = sorted((a.best_face.match_band.rank, b.best_face.match_band.rank))
            if bands[0] <= FaceMatchBand.LOW.rank and bands[1] >= FaceMatchBand.HIGH.rank:
                # One source's photo matches the target, the other's clearly does not:
                # they cannot both describe the target person.
                link.veto = "contradictory face evidence"
        pa, pb = a.page, b.page
        ca, cb = canonical_url(pa.url), canonical_url(pb.url)
        a_links = set(pa.profile.outbound_links) | {canonical_url(u) for u in pa.profile.social_links}
        b_links = set(pb.profile.outbound_links) | {canonical_url(u) for u in pb.profile.social_links}
        if cb in a_links or (pb.profile.canonical_url and pb.profile.canonical_url in a_links):
            link.strong.append(f"{_label(pa)} links to {pb.url}")
        if ca in b_links or (pa.profile.canonical_url and pa.profile.canonical_url in b_links):
            link.strong.append(f"{_label(pb)} links to {pa.url}")
        emails = {normalize_email(e) for e in pa.profile.emails} & {normalize_email(e) for e in pb.profile.emails}
        emails.discard("")
        if emails:
            link.strong.append(f"both list the email {min(emails)}")
        for x, y in ((pa, pb), (pb, pa)):
            y_domain = registrable_domain(y.url)
            if platform_for(y.url) is None and y_domain not in SOCIAL_DOMAINS:
                personal = {registrable_domain(u) for u in x.profile.social_links}
                if y_domain in personal:
                    link.strong.append(f"{_label(x)} lists the personal website {y_domain}")

        handles = {u for u in pa.profile.usernames if len(u) >= 4} & {u for u in pb.profile.usernames if len(u) >= 4}
        if handles:
            link.moderate.append(("username", f"same handle “{min(handles)}”"))
        na, nb = normalize_name(pa.profile.profile_name), normalize_name(pb.profile.profile_name)
        if na and na == nb and len(name_tokens(na)) >= 2:
            link.moderate.append(("name", f"same profile name “{pa.profile.profile_name}”"))
        a_hashes = [i.phash for i in pa.images if i.phash and i.faces]
        b_hashes = [i.phash for i in pb.images if i.phash and i.faces]
        if any(hamming_distance(x, y) <= 4 for x in a_hashes for y in b_hashes):
            link.moderate.append(("avatar", "same profile photo"))
        if a.best_face_embedding is not None and b.best_face_embedding is not None:
            cmp = self.faces.compare(a.best_face_embedding, b.best_face_embedding)
            if cmp.band.rank >= FaceMatchBand.HIGH.rank:
                link.moderate.append(("face", f"their photos show {cmp.band.value} face similarity to each other"))
        orgs = {normalize_text(o) for o in pa.profile.organizations} & {
            normalize_text(o) for o in pb.profile.organizations
        }
        orgs.discard("")
        if orgs:
            link.moderate.append(("organization", f"same organisation “{min(orgs)}”"))
        locs = {normalize_text(o) for o in pa.profile.locations} & {normalize_text(o) for o in pb.profile.locations}
        loc_ev_a = {
            e.text.expected for e in a.evidence if e.type == EvidenceType.LOCATION_MATCH and e.text and e.text.exact
        }
        loc_ev_b = {
            e.text.expected for e in b.evidence if e.type == EvidenceType.LOCATION_MATCH and e.text and e.text.exact
        }
        locs.discard("")
        if locs or (loc_ev_a & loc_ev_b):
            link.moderate.append(("location", "compatible location"))
        return link

    # ---------------------------------------------------------------- cluster
    def cluster(self, nodes: list[PageNode]) -> list[CandidateIdentity]:
        nodes = [n for n in nodes if n.relevant]
        parent = list(range(len(nodes)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        reasons: dict[tuple[int, int], Link] = {}
        for i, j in combinations(range(len(nodes)), 2):
            if canonical_url(nodes[i].page.url) == canonical_url(nodes[j].page.url):
                link = Link(strong=["same canonical URL"])
            else:
                link = self.link(nodes[i], nodes[j])
            if link.should_merge():
                reasons[(i, j)] = link
                parent[find(i)] = find(j)

        groups: dict[int, list[int]] = {}
        for i in range(len(nodes)):
            groups.setdefault(find(i), []).append(i)

        candidates = []
        for members in groups.values():
            member_set = set(members)
            group_links = [(i, j, lk) for (i, j), lk in reasons.items() if i in member_set and j in member_set]
            candidates.append(
                self._build([nodes[i] for i in members], [(nodes[i], nodes[j], lk) for i, j, lk in group_links])
            )
        return candidates

    def _build(self, members: list[PageNode], links: list[tuple[PageNode, PageNode, Link]]) -> CandidateIdentity:
        pages = [m.page for m in members]
        evidence: dict[str, Evidence] = {}
        for m in members:
            for ev in m.evidence:
                evidence.setdefault(ev.id, ev)
        cluster_reasons = []
        for a, b, lk in links:
            if lk.strong:
                for text in lk.strong:
                    ev = make_evidence(
                        EvidenceType.CROSS_LINK,
                        EvidenceStrength.STRONG,
                        f"Explicit connection: {text}.",
                        a.page.url,
                        related_url=b.page.url,
                    )
                    evidence.setdefault(ev.id, ev)
            descr = "; ".join(lk.strong + [d for _, d in lk.moderate])
            cluster_reasons.append(f"{_label(a.page)} ↔ {_label(b.page)}: {descr}")

        faces = [m.best_face for m in members if m.best_face is not None]
        best_face = min(faces, key=lambda f: f.distance) if faces else None
        ordered = sorted(pages, key=lambda p: source_quality(p.url), reverse=True)
        return CandidateIdentity(
            id=stable_hash(sorted(canonical_url(p.url) for p in pages), 12),
            display_name=self._display_name(ordered),
            page_ids=[p.id for p in ordered],
            urls=[p.url for p in ordered],
            domains=dedupe_preserve_order([domain_of(p.url) for p in ordered]),
            platforms=dedupe_preserve_order([p.platform for p in ordered if p.platform]),
            usernames=dedupe_preserve_order([u for p in ordered for u in p.profile.usernames])[:10],
            locations=dedupe_preserve_order([loc for p in ordered for loc in p.profile.locations])[:10],
            organizations=dedupe_preserve_order([o for p in ordered for o in p.profile.organizations])[:10],
            evidence=list(evidence.values()),
            best_face=best_face,
            cluster_reasons=cluster_reasons,
        )

    @staticmethod
    def _display_name(pages: list[CandidatePage]) -> str:
        names: dict[str, tuple[int, str]] = {}
        for page in pages:
            name = page.profile.profile_name
            if name and 1 <= len(name_tokens(name)) <= 6 and len(name) <= 80:
                key = normalize_name(name)
                count, original = names.get(key, (0, name))
                names[key] = (count + 1, original)
        if names:
            return max(names.values(), key=lambda v: v[0])[1]
        for page in pages:
            title = clean_title(page.title or page.profile.title)
            if title:
                return title
        return domain_of(pages[0].url) if pages else "Unknown"
