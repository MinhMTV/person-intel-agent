"""Tests for timeline API."""

import pytest
from fastapi.testclient import TestClient

from app.web import app, _dossiers, _search_history
from app.models import (
    PersonQuery, PersonDossier, SocialProfile, SearchResult,
    ImageMatch, Source, Confidence,
)


@pytest.fixture(autouse=True)
def clear_state():
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
    query = PersonQuery(full_name="John Smith")
    d = PersonDossier(query=query, confidence_score=0.8)
    d.social_profiles = [
        SocialProfile(platform="github", url="https://github.com/js", username="js", confidence=Confidence.HIGH, verified=True),
        SocialProfile(platform="twitter", url="https://twitter.com/js", username="js", confidence=Confidence.MEDIUM),
    ]
    d.web_results = [
        SearchResult(source=Source.WEB, title="Blog", url="https://blog.com/js", confidence=Confidence.HIGH),
    ]
    d.image_matches = [
        ImageMatch(source_url="https://twitter.com/js", image_url="https://img.com/1.jpg", similarity_score=0.92),
    ]
    d.email_addresses = ["john@example.com"]
    d.scanners_used = ["social", "web"]
    return d


class TestTimelineEndpoint:
    def test_get_timeline(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 5  # 2 social + 1 web + 1 image + 1 email
        assert data["name"] == "John Smith"
        assert len(data["timeline"]) == 5

    def test_timeline_not_found(self, client):
        resp = client.get("/api/timeline/nonexistent")
        assert resp.status_code == 404

    def test_filter_by_type(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1?type_filter=social")
        data = resp.json()
        assert data["total_events"] == 2
        assert all(t["type"] == "social" for t in data["timeline"])

    def test_filter_by_confidence(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1?confidence_filter=high")
        data = resp.json()
        assert all(t["confidence"] == "high" for t in data["timeline"])

    def test_filter_by_platform(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1?platform_filter=github")
        data = resp.json()
        assert all(t["platform"] == "github" for t in data["timeline"])

    def test_combined_filters(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1?type_filter=social&confidence_filter=high")
        data = resp.json()
        assert data["total_events"] == 1
        assert data["timeline"][0]["platform"] == "github"

    def test_limit(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1?limit=2")
        data = resp.json()
        assert data["total_events"] == 2

    def test_timeline_has_types(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1")
        data = resp.json()
        types = {t["type"] for t in data["timeline"]}
        assert "social" in types
        assert "web" in types
        assert "image" in types
        assert "email" in types

    def test_type_counts(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1")
        data = resp.json()
        assert data["type_counts"]["social"] == 2
        assert data["type_counts"]["web"] == 1


class TestTimelineSummary:
    def test_summary(self, client, sample_dossier):
        _dossiers["t1"] = sample_dossier
        resp = client.get("/api/timeline/t1/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_events"] == 5
        assert "github" in data["platforms"]
        assert data["high_confidence_count"] >= 2  # github social + web (+ possibly image)
        assert len(data["top_findings"]) >= 2

    def test_summary_not_found(self, client):
        resp = client.get("/api/timeline/nonexistent/summary")
        assert resp.status_code == 404
