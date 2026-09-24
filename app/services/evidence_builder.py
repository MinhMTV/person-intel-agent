"""Construction helpers for Evidence objects (deterministic ids)."""

from __future__ import annotations

from typing import Any

from app.domain.evidence import Evidence, EvidenceStrength, EvidenceType, FaceEvidence, FaceMatchBand
from app.domain.image import ImageDiscoveryResult, ImageMatchType
from app.utils.canonical import domain_of, stable_hash


def make_evidence(type_: EvidenceType, strength: EvidenceStrength, observation: str, source_url: str | None,
                  **fields: Any) -> Evidence:
    ev = Evidence(
        id="",
        type=type_,
        strength=strength,
        observation=observation,
        source_url=source_url,
        source_domain=domain_of(source_url) or None,
        **fields,
    )
    ev.id = stable_hash(ev.dedupe_key(), 16)
    return ev


def image_occurrence_evidence(hit: ImageDiscoveryResult, source_url: str) -> Evidence:
    if hit.match_type == ImageMatchType.EXACT:
        type_, strength = EvidenceType.EXACT_IMAGE, EvidenceStrength.STRONG
        what = "the reference photo (same image)"
    elif hit.match_type in (ImageMatchType.PARTIAL, ImageMatchType.MODIFIED):
        type_, strength = EvidenceType.PARTIAL_IMAGE, EvidenceStrength.STRONG
        what = "a cropped/modified copy of the reference photo" if hit.match_type == ImageMatchType.PARTIAL else \
            "an edited copy of the reference photo"
    else:
        type_, strength = EvidenceType.SIMILAR_IMAGE, EvidenceStrength.WEAK
        what = "a visually similar (different) image"
    via = "local perceptual-hash comparison" if hit.provider == "local_phash" else hit.provider
    return make_evidence(
        type_, strength, f"{source_url} contains {what} — reported by {via}.", source_url,
        provider=hit.provider, image=hit,
    )


_FACE_STRENGTH = {
    FaceMatchBand.VERY_HIGH: EvidenceStrength.STRONG,
    FaceMatchBand.HIGH: EvidenceStrength.STRONG,
    FaceMatchBand.MEDIUM: EvidenceStrength.MODERATE,
    FaceMatchBand.LOW: EvidenceStrength.WEAK,
    FaceMatchBand.NO_MATCH: EvidenceStrength.WEAK,
}


def face_evidence(face: FaceEvidence, source_url: str) -> Evidence:
    if face.match_band in (FaceMatchBand.LOW, FaceMatchBand.NO_MATCH):
        text = (f"A face on an image from this source was compared with the target face and does NOT look like "
                f"the same person (band {face.match_band.value}, {face.model} cosine similarity {face.cosine_similarity:.3f}).")
    else:
        text = (f"A face on an image from this source has {face.match_band.value.replace('_', ' ')} similarity to the "
                f"target face ({face.model} cosine similarity {face.cosine_similarity:.3f}, distance {face.distance:.3f}).")
    return make_evidence(EvidenceType.FACE_SIMILARITY, _FACE_STRENGTH[face.match_band], text, source_url,
                         provider=f"local:{face.model}", face=face)
