"""Tests for API filter endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.models import (
    PersonQuery, PersonDossier, SocialProfile, SearchResult,
    ImageMatch, Source, Confidence, Location,
)
from app.web import app, _dossiers, _search_history


@pytest.fixture(autouse=True)
def clear_state():
    """Clear in-memory state before each test."""
    _dossiers.clear()
    _search_history.clear()
    yield
    _dossiers.clear()
    _search_history.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sample_dossier():
    """Create a sample dossier for testing."""
    query = PersonQuery(
        full_name="John Smith",
        first_name="John",
        last_name="Smith",
    )
    d = PersonDossier(query=query, confidence_score=0.75)
    d.social_profiles = [
        SocialProfile(platform="github", url="https://github.com/js", username="js", confidence=Confidence.HIGH, verified=True),
        SocialProfile(platform="twitter", url="https://twitter.com/js", username="js", bio="Dev", confidence=Confidence.MEDIUM),
        SocialProfile(platform="linkedin", url="https://linkedin.com/in/js", username="js", confidence=Confidence.LOW),
    ]
    d.web_results = [
        SearchResult(source=Source.WEB, title="John Smith Blog", url="https://blog.com/js", confidence=Confidence.HIGH),
        SearchResult(source=Source.WEB, title="John Smith LinkedIn", url="https://linkedin.com/in/js2", confidence=Confidence.LOW),
    ]
    d.image_matches = [
        ImageMatch(source_url="https://twitter.com/js", image_url="https://img.com/1.jpg", similarity_score=0.92),
        ImageMatch(source_url="https://github.com/js", image_url="https://img.com/2.jpg", similarity_score=0.45),
    ]
    d.email_addresses = ["john@example.com", "jsmith@work.com"]
    d.scanners_used = ["social", "web", "email"]
    return d


class TestFilterEndpoint:
    def test_filter_by_platform(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/filter?platform=github")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["social_profiles"] == 1
        assert data["results"]["social_profiles"][0]["platform"] == "github"

    def test_filter_by_confidence(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/filter?confidence=high")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["social_profiles"] == 1  # Only github is high
        assert data["counts"]["web_results"] == 1

    def test_filter_verified_only(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/filter?verified_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["social_profiles"] == 1  # Only github is verified

    def test_filter_has_bio(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/filter?has_bio=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["social_profiles"] == 1  # Only twitter has bio

    def test_filter_min_similarity(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/filter?min_similarity=0.8")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["image_matches"] == 1  # Only 0.92 match

    def test_filter_not_found(self, client):
        resp = client.get("/api/v2/dossier/nonexistent/filter")
        assert resp.status_code == 404

    def test_filter_combined(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/filter?platform=github&confidence=high&verified_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["social_profiles"] == 1


class TestAnalyticsEndpoint:
    def test_analytics(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence_score"] == 0.75
        assert "platforms" in data
        assert "confidence_distribution" in data
        assert data["totals"]["social_profiles"] == 3

    def test_analytics_not_found(self, client):
        resp = client.get("/api/v2/dossier/nonexistent/analytics")
        assert resp.status_code == 404


class TestPlatformsEndpoint:
    def test_platforms(self, client, sample_dossier):
        _dossiers["test123"] = sample_dossier
        resp = client.get("/api/v2/dossier/test123/platforms")
        assert resp.status_code == 200
        data = resp.json()
        assert "github" in data["platforms"]
        assert "twitter" in data["platforms"]
        assert data["total_platforms"] == 3


class TestCompareEndpoint:
    def test_compare(self, client, sample_dossier):
        _dossiers["d1"] = sample_dossier

        d2 = PersonDossier(query=PersonQuery(full_name="Jane Doe", first_name="Jane", last_name="Doe"))
        d2.social_profiles = [
            SocialProfile(platform="github", url="https://github.com/jd", username="jd"),
            SocialProfile(platform="instagram", url="https://instagram.com/jd", username="jd"),
        ]
        _dossiers["d2"] = d2

        resp = client.get("/api/v2/search/compare?id1=d1&id2=d2")
        assert resp.status_code == 200
        data = resp.json()
        assert "github" in data["shared_platforms"]
        assert "twitter" in data["unique_to_1"]
        assert "instagram" in data["unique_to_2"]

    def test_compare_not_found(self, client):
        resp = client.get("/api/v2/search/compare?id1=a&id2=b")
        assert resp.status_code == 404
