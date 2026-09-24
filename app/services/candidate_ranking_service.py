"""Candidate ranking and the investigation-level conclusion text."""

from __future__ import annotations

from app.domain.candidate import CandidateIdentity, EvidenceLevel
from app.domain.identity import IdentityHints
from app.services.evidence_fusion_service import EvidenceFusionService


class CandidateRankingService:
    def __init__(self, fusion: EvidenceFusionService):
        self.fusion = fusion

    def rank(
        self, candidates: list[CandidateIdentity], hints: IdentityHints, face_available: bool
    ) -> list[CandidateIdentity]:
        for candidate in candidates:
            candidate.assessment = self.fusion.assess(candidate, hints, face_available)
            candidate.evidence.sort(
                key=lambda e: (e.strength.value != "STRONG", e.strength.value != "MODERATE", e.type.value)
            )
        ranked = [c for c in candidates if c.assessment.level != EvidenceLevel.INSUFFICIENT]
        ranked.sort(key=lambda c: (c.assessment.level.rank, c.assessment.score), reverse=True)
        for index, candidate in enumerate(ranked, start=1):
            candidate.rank = index
        return ranked

    @staticmethod
    def conclusion(candidates: list[CandidateIdentity]) -> str:
        strong = [c for c in candidates if c.assessment.level.rank >= EvidenceLevel.STRONG.rank]
        moderate = [c for c in candidates if c.assessment.level == EvidenceLevel.MODERATE]
        if strong:
            top = strong[0]
            extra = f" and {len(strong) - 1} other strong candidate(s)" if len(strong) > 1 else ""
            return (
                f"{len(strong)} candidate(s) with strong evidence. Top: {top.display_name} "
                f"({top.assessment.level.value}){extra}. Candidate matches must be verified independently."
            )
        if moderate:
            return (
                f"No strong candidate found. {len(moderate)} candidate(s) with moderate evidence need "
                "further verification."
            )
        if candidates:
            return f"No strong candidate found. Only {len(candidates)} weak lead(s)."
        return "No strong candidate found. No source with meaningful evidence was discovered."
