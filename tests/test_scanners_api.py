"""Tests for scanner status API."""

import pytest
from fastapi.testclient import TestClient
from app.web import app


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestScannerStatus:
    def test_status_endpoint(self, client):
        resp = client.get("/api/scanners/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "scanners" in data
        assert data["total"] == 11
        assert data["available"] > 0

    def test_all_scanners_listed(self, client):
        resp = client.get("/api/scanners/status")
        data = resp.json()
        ids = {s["id"] for s in data["scanners"]}
        assert "social" in ids
        assert "web" in ids
        assert "email" in ids
        assert "professional" in ids

    def test_scanner_has_required_fields(self, client):
        resp = client.get("/api/scanners/status")
        data = resp.json()
        for s in data["scanners"]:
            assert "id" in s
            assert "name" in s
            assert "description" in s
            assert "type" in s
            assert "status" in s

    def test_checked_at(self, client):
        resp = client.get("/api/scanners/status")
        data = resp.json()
        assert "checked_at" in data


class TestScannerList:
    def test_list_endpoint(self, client):
        resp = client.get("/api/scanners/list")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scanners"]) == 11

    def test_list_fields(self, client):
        resp = client.get("/api/scanners/list")
        data = resp.json()
        for s in data["scanners"]:
            assert "id" in s
            assert "name" in s
            assert "type" in s
