"""Tests for confidence scoring module."""

import pytest
from app.models import (
    SocialProfile, SearchResult, ImageMatch, PersonQuery,
    Source, Confidence,
)
from app.analysis.scoring import (
    score_social_profile,
    score_search_result,
    score_image_match,
    apply_confidence_scores,
    _username_match_score,
    _score_to_confidence,
    SOURCE_RELIABILITY,
)


@pytest.fixture
def query():
    return PersonQuery(
        full_name="John Smith",
        first_name="John",
        last_name="Smith",
        usernames=["johnsmith", "jsmith"],
        nicknames=["Johnny"],
    )


class TestSourceReliability:
    def test_linkedin_highest(self):
        assert SOURCE_RELIABILITY["linkedin"] > SOURCE_RELIABILITY["twitter"]

    def test_github_reliable(self):
        assert SOURCE_RELIABILITY["github"] >= 0.8

    def test_breach_low(self):
        assert SOURCE_RELIABILITY["breach"] < 0.5


class TestUsernameMatchScore:
    def test_exact_match(self, query):
        assert _username_match_score("johnsmith", query) == 1.0

    def test_case_insensitive(self, query):
        assert _username_match_score("JohnSmith", query) == 1.0

    def test_known_pattern(self, query):
        assert _username_match_score("john.smith", query) >= 0.8

    def test_nickname_match(self, query):
        score = _username_match_score("johnnysmith", query)
        assert score >= 0.5

    def test_partial_match(self, query):
        score = _username_match_score("johnthebuilder", query)
        assert score >= 0.4

    def test_no_match(self, query):
        assert _username_match_score("completelydifferent", query) == 0.1

    def test_empty(self, query):
        assert _username_match_score("", query) == 0.0


class TestScoreSocialProfile:
    def test_linkedin_verified(self, query):
        p = SocialProfile(
            platform="linkedin",
            url="https://linkedin.com/in/johnsmith",
            username="johnsmith",
            display_name="John Smith",
            bio="Software Engineer",
            verified=True,
        )
        score = score_social_profile(p, query)
        assert score >= 0.8

    def test_unknown_platform(self, query):
        p = SocialProfile(
            platform="unknown_site",
            url="https://unknown.com/user123",
            username="xyz123",
        )
        score = score_social_profile(p, query)
        assert score < 0.5

    def test_complete_profile_scores_higher(self, query):
        minimal = SocialProfile(platform="github", url="https://github.com/js", username="js")
        complete = SocialProfile(
            platform="github",
            url="https://github.com/js",
            username="johnsmith",
            display_name="John Smith",
            bio="Developer",
            followers=500,
        )
        assert score_social_profile(complete, query) > score_social_profile(minimal, query)


class TestScoreSearchResult:
    def test_name_in_title(self, query):
        r = SearchResult(
            source=Source.WEB,
            title="John Smith - Software Developer",
            url="https://example.com",
            snippet="About John Smith...",
        )
        score = score_search_result(r, query)
        assert score >= 0.5

    def test_no_name_match(self, query):
        r = SearchResult(
            source=Source.WEB,
            title="Random Article",
            url="https://example.com",
            snippet="Nothing relevant",
        )
        score = score_search_result(r, query)
        assert score < 0.3

    def test_gov_domain_bonus(self, query):
        r = SearchResult(
            source=Source.WEB,
            title="John Smith Government Record",
            url="https://agency.gov/people/smith",
        )
        score = score_search_result(r, query)
        assert score > 0  # Should have some score from domain


class TestScoreImageMatch:
    def test_high_similarity(self):
        m = ImageMatch(
            source_url="https://linkedin.com/in/john",
            image_url="https://img.com/photo.jpg",
            similarity_score=0.95,
        )
        assert score_image_match(m) >= 0.6

    def test_low_similarity(self):
        m = ImageMatch(
            source_url="https://random.com/page",
            image_url="https://img.com/photo.jpg",
            similarity_score=0.3,
        )
        assert score_image_match(m) < 0.3


class TestApplyConfidenceScores:
    def test_returns_sorted(self, query):
        profiles = [
            SocialProfile(platform="unknown", url="https://x.com/a", username="xyz"),
            SocialProfile(platform="linkedin", url="https://linkedin.com/in/johnsmith", username="johnsmith"),
        ]
        results = apply_confidence_scores(profiles, [], [], query)

        # Should be sorted by score descending
        scores = [s["score"] for s in results["social"]]
        assert scores == sorted(scores, reverse=True)

    def test_overall_confidence(self, query):
        profiles = [
            SocialProfile(platform="linkedin", url="https://linkedin.com/in/johnsmith", username="johnsmith"),
        ]
        results = apply_confidence_scores(profiles, [], [], query)
        assert 0 <= results["overall_confidence"] <= 1.0

    def test_counts(self, query):
        profiles = [
            SocialProfile(platform="linkedin", url="https://linkedin.com/in/johnsmith", username="johnsmith", verified=True),
            SocialProfile(platform="unknown", url="https://x.com/a", username="xyz123"),
        ]
        results = apply_confidence_scores(profiles, [], [], query)
        total = results["high_confidence_count"] + results["medium_confidence_count"] + results["low_confidence_count"]
        assert total == 2


class TestScoreToConfidence:
    def test_high(self):
        assert _score_to_confidence(0.8) == Confidence.HIGH

    def test_medium(self):
        assert _score_to_confidence(0.5) == Confidence.MEDIUM

    def test_low(self):
        assert _score_to_confidence(0.2) == Confidence.LOW
