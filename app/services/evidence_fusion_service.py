"""Evidence fusion: evidence items → interpretable assessment.

The output is a descriptive level (VERY_STRONG … INSUFFICIENT) derived from
explicit rules over *independent signal categories*, plus 0..1 dimension
sub-scores used for ranking. Nothing here is a calibrated probability.

Strong signal categories (each counts once):

* IMAGE       – the reference photo (exact / cropped / edited copy) occurs on a source
* FACE        – a face on the source has HIGH or VERY_HIGH similarity to the target
* IDENTIFIER  – a supplied username / email / URL matches exactly
* CONTEXT     – full-name match *plus* at least one context attribute
                (location, employer, education)
* CROSS_LINK  – explicit link between two sources of the candidate

Levels: ≥3 strong → VERY_STRONG · 2 → STRONG · 1 → MODERATE (or WEAK when the
single signal is face-only/context-only without support) · 0 → WEAK when any
supporting signal exists, else INSUFFICIENT.

Safety caps: a candidate whose compared faces clearly do NOT match the target
is capped at WEAK unless the reference photo itself occurs on its sources or an
identifier matches.
"""

from __future__ import annotations

from app.domain.candidate import Assessment, AssessmentDimensions, CandidateIdentity, EvidenceLevel, MatchState
from app.domain.evidence import Evidence, EvidenceStrength, EvidenceType, FaceMatchBand
from app.domain.identity import IdentityHints
from app.domain.image import ImageMatchType
from app.utils.canonical import registrable_domain
from app.utils.platforms import source_quality

_FACE_STRENGTH = {
    FaceMatchBand.VERY_HIGH: 1.0, FaceMatchBand.HIGH: 0.8, FaceMatchBand.MEDIUM: 0.5,
    FaceMatchBand.LOW: 0.15, FaceMatchBand.NO_MATCH: 0.0,
}
_TEXT_WEIGHTS = {
    EvidenceType.KNOWN_URL: 0.8, EvidenceType.EMAIL_MATCH: 0.7, EvidenceType.USERNAME_MATCH: 0.6,
    EvidenceType.EMPLOYER_MATCH: 0.25, EvidenceType.EDUCATION_MATCH: 0.25, EvidenceType.PROFESSION_MATCH: 0.1,
}
_CONTEXT = {EvidenceType.LOCATION_MATCH, EvidenceType.EMPLOYER_MATCH, EvidenceType.EDUCATION_MATCH}
_IDENTIFIERS = {EvidenceType.USERNAME_MATCH, EvidenceType.EMAIL_MATCH, EvidenceType.KNOWN_URL}


def _of(evidence: list[Evidence], *types: EvidenceType) -> list[Evidence]:
    return [e for e in evidence if e.type in types]


def _state(evidence: list[Evidence], type_: EvidenceType, supplied: bool) -> MatchState:
    if not supplied:
        return MatchState.UNKNOWN
    matches = _of(evidence, type_)
    if any(e.strength != EvidenceStrength.WEAK and (e.text is None or e.text.exact) for e in matches):
        return MatchState.YES
    return MatchState.PARTIAL if matches else MatchState.NO


class EvidenceFusionService:
    def assess(self, candidate: CandidateIdentity, hints: IdentityHints, face_matching_available: bool) -> Assessment:
        ev = candidate.evidence
        dims = AssessmentDimensions()
        reasons: list[str] = []
        caveats: list[str] = []
        strong: list[str] = []
        support = 0

        # --- image occurrence ---------------------------------------------------------
        image_types = [e.image.match_type for e in ev if e.image is not None and e.type != EvidenceType.FACE_SIMILARITY]
        image_occurrence = None
        for t in (ImageMatchType.EXACT, ImageMatchType.PARTIAL, ImageMatchType.MODIFIED, ImageMatchType.VISUALLY_SIMILAR):
            if t in image_types:
                image_occurrence = t
                break
        dims.image_occurrence_strength = {
            ImageMatchType.EXACT: 1.0, ImageMatchType.PARTIAL: 0.75, ImageMatchType.MODIFIED: 0.75,
            ImageMatchType.VISUALLY_SIMILAR: 0.2, None: 0.0,
        }[image_occurrence]
        if dims.image_occurrence_strength >= 0.75:
            strong.append("IMAGE")
            first = _of(ev, EvidenceType.EXACT_IMAGE, EvidenceType.PARTIAL_IMAGE)[0]
            reasons.append(first.observation)
            caveats.append("An image match shows that the PHOTO occurs on a page; it does not prove that every "
                           "name on that page refers to the pictured person.")
        elif image_occurrence == ImageMatchType.VISUALLY_SIMILAR:
            reasons.append("A visually similar (but different) image was found — weak on its own.")
            if candidate.best_face is None:
                # When the face on that image was compared, the face result supersedes
                # this observation (same image — not an independent signal).
                support += 1

        # --- face ------------------------------------------------------------------------
        face = candidate.best_face
        face_band = face.match_band if face else None
        dims.face_match_strength = _FACE_STRENGTH[face_band] if face_band else 0.0
        if face and face.match_band.rank >= FaceMatchBand.HIGH.rank:
            strong.append("FACE")
            reasons.append(f"Face similarity {face.match_band.value.replace('_', ' ')} "
                           f"({face.model}, cosine similarity {face.cosine_similarity:.3f}).")
            caveats.append("Face similarity is not proof of identity — look-alikes exist and the bands are "
                           "heuristic, not calibrated probabilities.")
        elif face and face.match_band == FaceMatchBand.MEDIUM:
            support += 1
            reasons.append(f"Face similarity MEDIUM ({face.model}, cosine similarity {face.cosine_similarity:.3f}) — "
                           "inconclusive on its own.")
        if not face_matching_available:
            caveats.append("Face identity matching was unavailable for this run.")

        # --- identity text -------------------------------------------------------------------
        text_strength = 0.0
        name_ev = _of(ev, EvidenceType.NAME_MATCH)
        name_exact = any(e.strength == EvidenceStrength.MODERATE for e in name_ev)
        if name_exact:
            text_strength += 0.35
        elif name_ev:
            text_strength += 0.15
        loc_ev = _of(ev, EvidenceType.LOCATION_MATCH)
        if loc_ev:
            text_strength += 0.2 if any(e.strength == EvidenceStrength.MODERATE for e in loc_ev) else 0.1
        for type_, weight in _TEXT_WEIGHTS.items():
            if _of(ev, type_):
                text_strength += weight
        dims.identity_text_strength = min(1.0, round(text_strength, 3))
        identifiers = [e for e in ev if e.type in _IDENTIFIERS and e.strength == EvidenceStrength.STRONG]
        if identifiers:
            strong.append("IDENTIFIER")
            reasons.append(identifiers[0].observation)
        context = [e for e in ev if e.type in _CONTEXT and e.strength != EvidenceStrength.WEAK]
        if name_exact and context:
            strong.append("CONTEXT")
            reasons.append(f"Full name matches together with {', '.join(sorted({e.type.value.split('_')[0].lower() for e in context}))}.")
        elif name_exact:
            support += 1
            reasons.append("The full name matches (names are often shared by different people).")
            caveats.append("A name match alone does not establish identity.")
        elif name_ev:
            support += 1
        if context and not name_exact:
            support += 1

        # --- cross-source ---------------------------------------------------------------------
        independent = {registrable_domain(e.source_url) for e in ev if e.source_url and not (
            e.type == EvidenceType.FACE_SIMILARITY and e.face and e.face.match_band.rank < FaceMatchBand.MEDIUM.rank)}
        independent.discard("")
        cross_links = _of(ev, EvidenceType.CROSS_LINK)
        dims.cross_source_strength = 1.0 if len(independent) >= 3 else 0.5 if len(independent) == 2 else 0.0
        if cross_links:
            dims.cross_source_strength = max(dims.cross_source_strength, 0.7)
            strong.append("CROSS_LINK")
            reasons.append(cross_links[0].observation)
        dims.source_quality = max((source_quality(u) for u in candidate.urls), default=0.0)

        # --- level ------------------------------------------------------------------------------
        n = len(strong)
        if n >= 3:
            level = EvidenceLevel.VERY_STRONG
        elif n == 2:
            level = EvidenceLevel.STRONG
        elif n == 1:
            only = strong[0]
            decisive = only in ("IMAGE", "IDENTIFIER") or (only == "FACE" and face_band == FaceMatchBand.VERY_HIGH)
            level = EvidenceLevel.MODERATE if decisive or support >= 1 else EvidenceLevel.WEAK
        else:
            level = EvidenceLevel.WEAK if support else EvidenceLevel.INSUFFICIENT

        # Contradiction cap: faces were compared and clearly look different.
        if face_band in (FaceMatchBand.LOW, FaceMatchBand.NO_MATCH) and not {"IMAGE", "IDENTIFIER"} & set(strong):
            if level.rank > EvidenceLevel.WEAK.rank:
                level = EvidenceLevel.WEAK
            caveats.append("Faces on this candidate's photos were compared with the target and do NOT look alike.")

        score = (
            level.rank * 100
            + 40 * dims.image_occurrence_strength
            + 35 * dims.face_match_strength
            + 30 * dims.identity_text_strength
            + 15 * dims.cross_source_strength
            + 10 * dims.source_quality
        )
        return Assessment(
            level=level,
            score=round(score, 2),
            dimensions=dims,
            strong_signals=strong,
            independent_sources=len(independent),
            image_occurrence=image_occurrence,
            face_band=face_band,
            name_match=_state(ev, EvidenceType.NAME_MATCH, bool(hints.name)),
            location_match=_state(ev, EvidenceType.LOCATION_MATCH, bool(hints.location or hints.country)),
            username_match=_state(ev, EvidenceType.USERNAME_MATCH, bool(hints.usernames)),
            email_match=_state(ev, EvidenceType.EMAIL_MATCH, bool(hints.emails)),
            employer_match=_state(ev, EvidenceType.EMPLOYER_MATCH, bool(hints.employer)),
            education_match=_state(ev, EvidenceType.EDUCATION_MATCH, bool(hints.university)),
            reasons=reasons,
            caveats=list(dict.fromkeys(caveats)),
        )
