"""Tests for dossier diff module."""

import pytest
from app.diff import diff_dossiers, diff_summary


@pytest.fixture
def base_dossier():
    return {
        "confidence_score": 0.75,
        "social_profiles": [
            {"platform": "twitter", "url": "https://twitter.com/johndoe", "confidence": "high"},
            {"platform": "linkedin", "url": "https://linkedin.com/in/johndoe", "confidence": "medium"},
        ],
        "web_results": [
            {"source": "google", "url": "https://example.com", "title": "John Doe"},
        ],
        "image_matches": [],
        "email_addresses": ["john@example.com"],
        "professional": [
            {"platform": "linkedin", "url": "https://linkedin.com/in/johndoe", "title": "Engineer"},
        ],
    }


class TestDiffDossiers:
    def test_no_changes(self, base_dossier):
        diff = diff_dossiers(base_dossier, base_dossier)
        assert diff["has_changes"] is False
        assert diff["confidence_change"] == 0.0

    def test_new_social_profile(self, base_dossier):
        new = dict(base_dossier)
        new["social_profiles"] = base_dossier["social_profiles"] + [
            {"platform": "github", "url": "https://github.com/johndoe", "confidence": "high"},
        ]
        diff = diff_dossiers(base_dossier, new)
        assert diff["has_changes"] is True
        assert len(diff["social_profiles"]["added"]) == 1
        assert len(diff["social_profiles"]["removed"]) == 0

    def test_removed_social_profile(self, base_dossier):
        new = dict(base_dossier)
        new["social_profiles"] = base_dossier["social_profiles"][:1]
        diff = diff_dossiers(base_dossier, new)
        assert diff["has_changes"] is True
        assert len(diff["social_profiles"]["removed"]) == 1

    def test_new_email(self, base_dossier):
        new = dict(base_dossier)
        new["email_addresses"] = ["john@example.com", "john.doe@company.com"]
        diff = diff_dossiers(base_dossier, new)
        assert diff["has_changes"] is True
        assert "john.doe@company.com" in diff["email_addresses"]["added"]

    def test_confidence_increase(self, base_dossier):
        new = dict(base_dossier)
        new["confidence_score"] = 0.90
        diff = diff_dossiers(base_dossier, new)
        assert diff["has_changes"] is True
        assert diff["confidence_change"] == pytest.approx(0.15)

    def test_confidence_decrease(self, base_dossier):
        new = dict(base_dossier)
        new["confidence_score"] = 0.50
        diff = diff_dossiers(base_dossier, new)
        assert diff["has_changes"] is True
        assert diff["confidence_change"] == pytest.approx(-0.25)


class TestDiffSummary:
    def test_no_changes_summary(self, base_dossier):
        diff = diff_dossiers(base_dossier, base_dossier)
        summary = diff_summary(diff)
        assert "No changes" in summary

    def test_changes_summary(self, base_dossier):
        new = dict(base_dossier)
        new["confidence_score"] = 0.90
        new["social_profiles"] = base_dossier["social_profiles"] + [
            {"platform": "github", "url": "https://github.com/johndoe", "confidence": "high"},
        ]
        diff = diff_dossiers(base_dossier, new)
        summary = diff_summary(diff)
        assert "Confidence" in summary
        assert "Social Profiles" in summary
