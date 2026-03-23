"""Tests for deduplication module."""

import pytest
from app.models import SocialProfile, SearchResult, ImageMatch, Source, Confidence
from app.analysis.dedup import (
    normalize_url,
    normalize_email,
    normalize_username,
    dedup_social_profiles,
    dedup_search_results,
    dedup_image_matches,
    dedup_emails,
    _confidence_rank,
)


class TestNormalizeUrl:
    def test_basic(self):
        assert normalize_url("https://github.com/test") == "https://github.com/test"

    def test_trailing_slash(self):
        assert normalize_url("https://github.com/test/") == "https://github.com/test"

    def test_www_stripped(self):
        assert normalize_url("https://www.example.com/path") == "https://example.com/path"

    def test_case_insensitive(self):
        assert normalize_url("https://GitHub.com/Test") == "https://github.com/test"

    def test_empty(self):
        assert normalize_url("") == ""

    def test_whitespace(self):
        assert normalize_url("  https://example.com  ") == "https://example.com"


class TestNormalizeEmail:
    def test_basic(self):
        assert normalize_email("John@Example.COM") == "john@example.com"

    def test_plus_alias(self):
        assert normalize_email("john+spam@gmail.com") == "john@gmail.com"

    def test_whitespace(self):
        assert normalize_email("  test@test.com  ") == "test@test.com"


class TestNormalizeUsername:
    def test_at_prefix(self):
        assert normalize_username("@john") == "john"

    def test_case(self):
        assert normalize_username("JohnSmith") == "johnsmith"


class TestDedupSocialProfiles:
    def test_no_dupes(self):
        profiles = [
            SocialProfile(platform="github", url="https://github.com/a"),
            SocialProfile(platform="twitter", url="https://twitter.com/b"),
        ]
        result = dedup_social_profiles(profiles)
        assert len(result) == 2

    def test_url_dedupe(self):
        profiles = [
            SocialProfile(platform="github", url="https://github.com/test", confidence=Confidence.LOW),
            SocialProfile(platform="github", url="https://github.com/test/", confidence=Confidence.HIGH),
        ]
        result = dedup_social_profiles(profiles)
        assert len(result) == 1
        assert result[0].confidence == Confidence.HIGH

    def test_username_dedupe(self):
        profiles = [
            SocialProfile(platform="github", url="https://github.com/john", username="john", confidence=Confidence.LOW),
            SocialProfile(platform="github", url="https://github.com/john", username="john", confidence=Confidence.HIGH),
        ]
        result = dedup_social_profiles(profiles)
        assert len(result) == 1

    def test_different_platforms_same_username(self):
        profiles = [
            SocialProfile(platform="github", url="https://github.com/john", username="john"),
            SocialProfile(platform="twitter", url="https://twitter.com/john", username="john"),
        ]
        result = dedup_social_profiles(profiles)
        assert len(result) == 2  # Different platforms, not duplicates


class TestDedupSearchResults:
    def test_url_dedupe(self):
        results = [
            SearchResult(source=Source.WEB, title="Test", url="https://example.com/page", snippet="Short"),
            SearchResult(source=Source.WEB, title="Test", url="https://example.com/page/", snippet="Longer better snippet"),
        ]
        result = dedup_search_results(results)
        assert len(result) == 1

    def test_prefer_snippet(self):
        results = [
            SearchResult(source=Source.WEB, title="A", url="https://a.com"),
            SearchResult(source=Source.WEB, title="A", url="https://a.com", snippet="Has snippet"),
        ]
        result = dedup_search_results(results)
        assert len(result) == 1
        assert result[0].snippet == "Has snippet"


class TestDedupImageMatches:
    def test_dedupe(self):
        matches = [
            ImageMatch(source_url="https://a.com", image_url="https://img.com/1.jpg", similarity_score=0.8),
            ImageMatch(source_url="https://a.com", image_url="https://img.com/1.jpg", similarity_score=0.9),
        ]
        result = dedup_image_matches(matches)
        assert len(result) == 1

    def test_sorted_by_similarity(self):
        matches = [
            ImageMatch(source_url="https://a.com", image_url="https://img.com/1.jpg", similarity_score=0.5),
            ImageMatch(source_url="https://b.com", image_url="https://img.com/2.jpg", similarity_score=0.9),
        ]
        result = dedup_image_matches(matches)
        assert result[0].similarity_score == 0.9


class TestDedupEmails:
    def test_case_insensitive(self):
        emails = ["John@Test.com", "john@test.com", "JOHN@TEST.COM"]
        result = dedup_emails(emails)
        assert len(result) == 1

    def test_plus_alias(self):
        emails = ["john@gmail.com", "john+spam@gmail.com"]
        result = dedup_emails(emails)
        assert len(result) == 1


class TestConfidenceRank:
    def test_ordering(self):
        assert _confidence_rank(Confidence.HIGH) > _confidence_rank(Confidence.MEDIUM)
        assert _confidence_rank(Confidence.MEDIUM) > _confidence_rank(Confidence.LOW)
