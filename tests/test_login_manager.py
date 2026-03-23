"""Tests for login manager module."""

import json
import pytest
from pathlib import Path
from app.login_manager import (
    get_session_status, get_all_sessions, save_cookies,
    load_cookies, delete_session, import_cookies_from_json,
    export_cookies_json, PLATFORMS, _session_file,
)


@pytest.fixture(autouse=True)
def cleanup_sessions():
    """Clean up session files after each test."""
    yield
    for platform in PLATFORMS:
        path = _session_file(platform)
        if path.exists():
            path.unlink()


class TestSessionStatus:
    def test_unknown_platform(self):
        result = get_session_status("unknown")
        assert "error" in result

    def test_not_logged_in(self):
        result = get_session_status("linkedin")
        assert result["status"] == "not_logged_in"

    def test_all_sessions_returns_all_platforms(self):
        sessions = get_all_sessions()
        assert "linkedin" in sessions
        assert "xing" in sessions
        assert "instagram" in sessions


class TestSaveLoadCookies:
    def test_save_and_load(self):
        cookies = [{"name": "sessionid", "value": "abc123", "domain": ".linkedin.com"}]
        assert save_cookies("linkedin", cookies) is True
        loaded = load_cookies("linkedin")
        assert loaded is not None
        assert loaded[0]["name"] == "sessionid"

    def test_save_invalid_platform(self):
        assert save_cookies("unknown", []) is False

    def test_load_nonexistent(self):
        assert load_cookies("linkedin") is None

    def test_delete_session(self):
        cookies = [{"name": "test", "value": "x"}]
        save_cookies("linkedin", cookies)
        assert delete_session("linkedin") is True
        assert load_cookies("linkedin") is None

    def test_delete_nonexistent(self):
        assert delete_session("linkedin") is False


class TestImportExport:
    def test_import_valid_json(self):
        cookies_json = json.dumps([{"name": "sid", "value": "123"}])
        result = import_cookies_from_json("xing", cookies_json)
        assert result["success"] is True
        assert result["cookie_count"] == 1

    def test_import_invalid_json(self):
        result = import_cookies_from_json("xing", "not json")
        assert "error" in result

    def test_import_not_array(self):
        result = import_cookies_from_json("xing", '{"key": "value"}')
        assert "error" in result

    def test_export_nonexistent(self):
        assert export_cookies_json("linkedin") is None

    def test_export_after_save(self):
        cookies = [{"name": "test", "value": "x"}]
        save_cookies("instagram", cookies)
        exported = export_cookies_json("instagram")
        assert exported is not None
        parsed = json.loads(exported)
        assert parsed[0]["name"] == "test"
