"""Integration tests — verify full pipeline with mock data.

Tests the end-to-end flow:
  PersonQuery → Scanners → Dedup → Confidence Scoring → Dossier
"""

import pytest
from unittest.mock import patch, AsyncMock
from app.models import (
    PersonQuery, PersonDossier, SocialProfile, SearchResult,
    ImageMatch, Source, Confidence, Location,
)
from app.analysis.dedup import dedup_all
from app.analysis.scoring import apply_confidence_scores


@pytest.fixture
def query():
    return PersonQuery(
        full_name="John Smith",
        first_name="John",
        last_name="Smith",
        usernames=["johnsmith"],
        nicknames=["Johnny"],
        locations=[Location(raw="Berlin", city="Berlin", country="Germany")],
    )


class TestDedupThenScore:
    """Test that dedup + scoring work together correctly."""

    def test_full_pipeline(self, query):
        # Simulate scanner results with duplicates
        social = [
            SocialProfile(platform="github", url="https://github.com/johnsmith", username="johnsmith", bio="Dev"),
            SocialProfile(platform="github", url="https://github.com/johnsmith/", username="johnsmith"),  # dupe
            SocialProfile(platform="linkedin", url="https://linkedin.com/in/johnsmith", username="johnsmith", verified=True),
            SocialProfile(platform="twitter", url="https://twitter.com/jsmith", username="jsmith"),
        ]
        web = [
            SearchResult(source=Source.WEB, title="John Smith - Developer", url="https://example.com/profile", snippet="About John Smith"),
            SearchResult(source=Source.WEB, title="John Smith - Developer", url="https://example.com/profile/"),  # dupe
            SearchResult(source=Source.WEB, title="J. Smith Conference Talk", url="https://conf.eu/speakers/jsmith"),
        ]

        # Step 1: Dedup
        deduped = dedup_all(social=social, web=web, images=[], emails=[], professional=[], academic=[])

        assert len(deduped["social_profiles"]) == 3  # GitHub dupe removed
        assert len(deduped["web_results"]) == 2  # URL dupe removed
        assert deduped["removed"]["social"] == 1
        assert deduped["removed"]["web"] == 1

        # Step 2: Score
        scored = apply_confidence_scores(
            social_profiles=deduped["social_profiles"],
            web_results=deduped["web_results"],
            image_matches=[],
            query=query,
        )

        # LinkedIn (verified + exact username) should score highest
        social_scores = {s["profile"].platform: s["score"] for s in scored["social"]}
        assert social_scores.get("linkedin", 0) > social_scores.get("twitter", 0)

        # Overall confidence should be reasonable
        assert 0 < scored["overall_confidence"] <= 1.0

    def test_dedup_preserves_higher_confidence(self, query):
        profiles = [
            SocialProfile(platform="github", url="https://github.com/john", username="john", confidence=Confidence.LOW),
            SocialProfile(platform="github", url="https://github.com/john/", username="john", confidence=Confidence.HIGH),
        ]
        deduped = dedup_all(social=profiles, web=[], images=[], emails=[], professional=[], academic=[])
        assert len(deduped["social_profiles"]) == 1
        assert deduped["social_profiles"][0].confidence == Confidence.HIGH

    def test_email_dedup_with_scoring(self, query):
        emails = ["john@smith.com", "John@Smith.com", "john+newsletter@smith.com"]
        deduped = dedup_all(social=[], web=[], images=[], emails=emails, professional=[], academic=[])
        assert len(deduped["email_addresses"]) == 1

    def test_image_dedup_sorted_by_similarity(self, query):
        images = [
            ImageMatch(source_url="https://a.com", image_url="https://img.com/1.jpg", similarity_score=0.5),
            ImageMatch(source_url="https://b.com", image_url="https://img.com/2.jpg", similarity_score=0.95),
            ImageMatch(source_url="https://a.com", image_url="https://img.com/1.jpg", similarity_score=0.8),  # dupe
        ]
        deduped = dedup_all(social=[], web=[], images=images, emails=[], professional=[], academic=[])
        assert len(deduped["image_matches"]) == 2
        # Should be sorted by similarity descending
        assert deduped["image_matches"][0].similarity_score >= deduped["image_matches"][1].similarity_score


class TestDossierGeneration:
    """Test dossier assembly from scored results."""

    def test_dossier_confidence(self, query):
        social = [
            SocialProfile(platform="linkedin", url="https://linkedin.com/in/johnsmith", username="johnsmith", verified=True),
            SocialProfile(platform="github", url="https://github.com/johnsmith", username="johnsmith"),
        ]
        web = [
            SearchResult(source=Source.WEB, title="John Smith Portfolio", url="https://johnsmith.dev"),
        ]

        # Dedup + Score
        deduped = dedup_all(social=social, web=web, images=[], emails=[], professional=[], academic=[])
        scored = apply_confidence_scores(
            deduped["social_profiles"], deduped["web_results"], deduped["image_matches"], query,
        )

        # Build dossier
        dossier = PersonDossier(query=query)
        dossier.social_profiles = [s["profile"] for s in scored["social"]]
        dossier.web_results = [s["result"] for s in scored["web"]]
        dossier.confidence_score = scored["overall_confidence"]

        assert dossier.confidence_score > 0
        assert len(dossier.social_profiles) == 2
        summary = dossier.summary()
        assert "John Smith" in summary
        assert "linkedin" in summary.lower()

    def test_empty_dossier(self, query):
        deduped = dedup_all(social=[], web=[], images=[], emails=[], professional=[], academic=[])
        scored = apply_confidence_scores([], [], [], query)
        assert scored["overall_confidence"] == 0.0
        assert scored["high_confidence_count"] == 0


class TestLocationIntegration:
    """Test that location expansion works in scanner context."""

    def test_location_expands_in_search(self):
        from app.analysis.location import build_location_queries
        queries = build_location_queries("John Smith", ["Oberhausen"])
        assert "John Smith Oberhausen" in queries
        assert "John Smith NRW" in queries
        assert "John Smith Germany" in queries

    def test_multiple_locations(self):
        from app.analysis.location import build_location_queries
        queries = build_location_queries("John Smith", ["Oberhausen", "Wien"])
        # Both countries should appear
        assert any("Germany" in q for q in queries)
        assert any("Austria" in q for q in queries)
