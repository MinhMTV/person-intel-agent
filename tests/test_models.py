"""Tests for Person Intelligence Agent models."""

import pytest
from datetime import datetime
from app.models import (
    Source, Confidence, Location, SocialProfile, SearchResult,
    ImageMatch, PersonQuery, PersonDossier,
)


class TestLocation:
    def test_basic(self):
        loc = Location(raw="Oberhausen, NRW")
        assert loc.raw == "Oberhausen, NRW"
        assert loc.city is None
        assert loc.geo_expansion == []

    def test_with_details(self):
        loc = Location(raw="Berlin", city="Berlin", state="Berlin", country="Germany", country_code="DE")
        assert loc.city == "Berlin"
        assert loc.country_code == "DE"

    def test_geo_expansion(self):
        loc = Location(raw="Oberhausen", geo_expansion=["NRW", "Ruhrgebiet", "Germany"])
        assert len(loc.geo_expansion) == 3


class TestSocialProfile:
    def test_basic(self):
        p = SocialProfile(platform="github", url="https://github.com/test")
        assert p.platform == "github"
        assert p.confidence == Confidence.MEDIUM
        assert p.verified is False

    def test_full(self):
        p = SocialProfile(
            platform="twitter",
            url="https://twitter.com/test",
            username="test",
            display_name="Test User",
            bio="Developer",
            followers=1000,
            verified=True,
            confidence=Confidence.HIGH,
        )
        assert p.verified is True
        assert p.followers == 1000


class TestSearchResult:
    def test_basic(self):
        r = SearchResult(source=Source.WEB, title="Test", url="https://example.com")
        assert r.source == Source.WEB
        assert r.confidence == Confidence.MEDIUM

    def test_source_enum(self):
        assert Source.WEB.value == "web"
        assert Source.SOCIAL.value == "social"
        assert Source.LINKEDIN.value == "linkedin"


class TestPersonQuery:
    def test_from_name(self):
        q = PersonQuery(full_name="John Smith")
        assert q.full_name == "John Smith"
        assert q.first_name is None  # not auto-split

    def test_with_details(self):
        q = PersonQuery(
            full_name="John Smith",
            first_name="John",
            last_name="Smith",
            nicknames=["Johnny"],
            usernames=["jsmith"],
            countries=["DE"],
        )
        assert q.nicknames == ["Johnny"]
        assert q.usernames == ["jsmith"]

    def test_with_locations(self):
        locs = [Location(raw="Berlin"), Location(raw="Munich")]
        q = PersonQuery(full_name="Test", locations=locs)
        assert len(q.locations) == 2


class TestPersonDossier:
    def test_empty(self):
        q = PersonQuery(full_name="Test")
        d = PersonDossier(query=q)
        assert d.social_profiles == []
        assert d.web_results == []
        assert d.confidence_score == 0.0

    def test_summary(self):
        q = PersonQuery(full_name="John Smith")
        d = PersonDossier(query=q)
        d.social_profiles.append(SocialProfile(platform="github", url="https://github.com/js"))
        summary = d.summary()
        assert "John Smith" in summary
        assert "github" in summary

    def test_with_results(self):
        q = PersonQuery(full_name="Test Person")
        d = PersonDossier(query=q)
        d.social_profiles = [
            SocialProfile(platform="twitter", url="https://twitter.com/test"),
            SocialProfile(platform="github", url="https://github.com/test"),
        ]
        d.web_results = [
            SearchResult(source=Source.WEB, title="About Test", url="https://example.com"),
        ]
        assert len(d.social_profiles) == 2
        assert len(d.web_results) == 1


class TestImageMatch:
    def test_basic(self):
        m = ImageMatch(source_url="https://example.com", image_url="https://img.com/1.jpg", similarity_score=0.85)
        assert m.similarity_score == 0.85
