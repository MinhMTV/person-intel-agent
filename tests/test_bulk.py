"""Tests for bulk search API."""

import io
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.web import app, _dossiers, _search_history
from app.models import PersonDossier, PersonQuery
from app.api_bulk import _bulk_jobs, BulkSearchJob, BulkSearchResult


@pytest.fixture(autouse=True)
def clear_state():
    _dossiers.clear()
    _search_history.clear()
    _bulk_jobs.clear()
    yield
    _dossiers.clear()
    _search_history.clear()
    _bulk_jobs.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestBulkUpload:
    def test_invalid_file_type(self, client):
        resp = client.post("/api/bulk/upload", files={"file": ("test.txt", b"hello", "text/plain")})
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_csv_upload(self, client):
        csv_content = "name,locations,usernames\nJohn Smith,Berlin,jsmith\nJane Doe,Munich,jdoe"
        resp = client.post("/api/bulk/upload",
            files={"file": ("people.csv", csv_content.encode(), "text/csv")})
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["total"] == 2

    def test_too_many_rows(self, client):
        rows = "\n".join(["name"] + [f"Person {i}" for i in range(101)])
        resp = client.post("/api/bulk/upload",
            files={"file": ("big.csv", rows.encode(), "text/csv")})
        assert resp.status_code == 200
        assert "error" in resp.json()


class TestBulkStatus:
    def test_job_not_found(self, client):
        resp = client.get("/api/bulk/status/nonexistent")
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_job_status(self, client):
        job = BulkSearchJob(id="test_job", status="running", total=5, completed=2)
        _bulk_jobs["test_job"] = job

        resp = client.get("/api/bulk/status/test_job")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["progress"] == 2


class TestBulkJobs:
    def test_list_empty(self, client):
        resp = client.get("/api/bulk/jobs")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    def test_list_jobs(self, client):
        _bulk_jobs["j1"] = BulkSearchJob(id="j1", status="completed", total=3, completed=3)
        _bulk_jobs["j2"] = BulkSearchJob(id="j2", status="running", total=5, completed=2)

        resp = client.get("/api/bulk/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["jobs"]) == 2


class TestBulkSearchResult:
    def test_auto_timestamp(self):
        r = BulkSearchResult(row=1, name="Test", status="success")
        assert r.timestamp is not None

    def test_with_dossier(self):
        r = BulkSearchResult(row=1, name="Test", status="success", dossier_id="abc123", results_count=5)
        assert r.dossier_id == "abc123"
        assert r.results_count == 5
