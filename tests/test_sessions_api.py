"""Tests for session management API."""

import json

import pytest
from fastapi.testclient import TestClient

from app.login_manager import PLATFORMS, _session_file
from app.web import app


@pytest.fixture(autouse=True)
def cleanup_sessions():
    yield
    for platform in PLATFORMS:
        path = _session_file(platform)
        if path.exists():
            path.unlink()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestSessionsApi:
    def test_list_sessions(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "linkedin" in data["sessions"]
        assert "facebook" in data["sessions"]

    def test_login_instructions(self, client):
        resp = client.get("/api/sessions/instagram/login")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Instagram"
        assert "login_url" in data

    def test_upload_cookies(self, client):
        resp = client.post(
            "/api/sessions/linkedin/cookies",
            json={"cookies": json.dumps([{"name": "li_at", "value": "abc", "domain": ".linkedin.com"}])},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["cookie_count"] == 1

    def test_delete_session(self, client):
        client.post(
            "/api/sessions/xing/cookies",
            json={"cookies": json.dumps([{"name": "sid", "value": "123", "domain": ".xing.com"}])},
        )
        resp = client.delete("/api/sessions/xing")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_start_login(self, client, monkeypatch):
        async def fake_login(platform, timeout_seconds=300):
            return {"platform": platform, "success": True, "name": "LinkedIn", "cookie_count": 2}

        monkeypatch.setattr("app.web.start_interactive_login", fake_login)
        resp = client.post("/api/sessions/linkedin/login")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["cookie_count"] == 2
