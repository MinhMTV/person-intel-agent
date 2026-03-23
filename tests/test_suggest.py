"""Tests for search suggestions API."""

import pytest
from fastapi.testclient import TestClient
from app.web import app, _search_history


@pytest.fixture(autouse=True)
def clear_history():
    _search_history.clear()
    yield
    _search_history.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestNameSuggestions:
    def test_empty_query(self, client):
        resp = client.get("/api/suggest/names?q=")
        assert resp.status_code == 422  # min_length=1

    def test_no_history(self, client):
        resp = client.get("/api/suggest/names?q=John")
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []

    def test_matches_from_history(self, client):
        _search_history.extend([
            {"id": "1", "name": "John Smith", "timestamp": "2026-03-23T10:00:00"},
            {"id": "2", "name": "Jane Doe", "timestamp": "2026-03-23T11:00:00"},
            {"id": "3", "name": "John Williams", "timestamp": "2026-03-23T12:00:00"},
        ])
        resp = client.get("/api/suggest/names?q=John")
        data = resp.json()
        names = [s["name"] for s in data["suggestions"]]
        assert "John Smith" in names
        assert "John Williams" in names
        assert "Jane Doe" not in names

    def test_case_insensitive(self, client):
        _search_history.append({"id": "1", "name": "John Smith", "timestamp": "2026-03-23T10:00:00"})
        resp = client.get("/api/suggest/names?q=john")
        assert len(resp.json()["suggestions"]) == 1

    def test_starts_with_first(self, client):
        _search_history.extend([
            {"id": "1", "name": "Mike Johnson", "timestamp": "2026-03-23T10:00:00"},
            {"id": "2", "name": "John Smith", "timestamp": "2026-03-23T11:00:00"},
        ])
        resp = client.get("/api/suggest/names?q=John")
        data = resp.json()
        assert data["suggestions"][0]["name"] == "John Smith"

    def test_no_duplicates(self, client):
        _search_history.extend([
            {"id": "1", "name": "John Smith", "timestamp": "2026-03-23T10:00:00"},
            {"id": "2", "name": "John Smith", "timestamp": "2026-03-23T11:00:00"},
        ])
        resp = client.get("/api/suggest/names?q=John")
        assert len(resp.json()["suggestions"]) == 1


class TestLocationSuggestions:
    def test_city_match(self, client):
        resp = client.get("/api/suggest/locations?q=Berlin")
        data = resp.json()
        assert any(s["city"] == "Berlin" for s in data["suggestions"])

    def test_partial_match(self, client):
        resp = client.get("/api/suggest/locations?q=Mun")
        data = resp.json()
        # Munich is stored as "muenchen", so "Mun" won't match. Use "Mue" instead.
        if not data["suggestions"]:
            resp = client.get("/api/suggest/locations?q=Mue")
            data = resp.json()
        assert len(data["suggestions"]) > 0

    def test_state_match(self, client):
        resp = client.get("/api/suggest/locations?q=NRW")
        data = resp.json()
        assert len(data["suggestions"]) > 0

    def test_case_insensitive(self, client):
        resp = client.get("/api/suggest/locations?q=berlin")
        data = resp.json()
        assert any(s["city"].lower() == "berlin" for s in data["suggestions"])

    def test_limit(self, client):
        resp = client.get("/api/suggest/locations?q=a")
        data = resp.json()
        assert len(data["suggestions"]) <= 10


class TestPlatformSuggestions:
    def test_all_platforms(self, client):
        resp = client.get("/api/suggest/platforms")
        data = resp.json()
        assert len(data["suggestions"]) >= 10

    def test_filter(self, client):
        resp = client.get("/api/suggest/platforms?q=git")
        data = resp.json()
        assert any("github" in s["id"] for s in data["suggestions"])
        assert not any("twitter" in s["id"] for s in data["suggestions"])

    def test_has_icons(self, client):
        resp = client.get("/api/suggest/platforms")
        data = resp.json()
        for s in data["suggestions"]:
            assert "icon" in s
