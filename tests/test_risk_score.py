"""Tests for risk score module."""

import pytest
from app.analysis.risk_score import calculate_risk_score


@pytest.fixture
def empty_dossier():
    return {
        "confidence_score": 0.0,
        "social_profiles": [],
        "web_results": [],
        "image_matches": [],
        "email_addresses": [],
        "professional": [],
    }


@pytest.fixture
def exposed_dossier():
    return {
        "confidence_score": 0.85,
        "social_profiles": [
            {"platform": "twitter", "url": "https://twitter.com/jd"},
            {"platform": "linkedin", "url": "https://linkedin.com/in/jd"},
            {"platform": "instagram", "url": "https://instagram.com/jd"},
            {"platform": "github", "url": "https://github.com/jd"},
        ],
        "web_results": [
            {"source": "google", "url": f"https://example.com/{i}", "title": f"Result {i}"}
            for i in range(10)
        ],
        "image_matches": [
            {"platform": "google", "url": "https://img.example.com/photo.jpg", "similarity": 0.9},
        ],
        "email_addresses": ["john@example.com", "jd@company.com"],
        "professional": [
            {"platform": "linkedin", "url": "https://linkedin.com/in/jd", "title": "Engineer"},
        ],
    }


class TestRiskScore:
    def test_empty_dossier_low_risk(self, empty_dossier):
        risk = calculate_risk_score(empty_dossier)
        assert risk.score < 25
        assert risk.level == "low"

    def test_exposed_dossier_high_risk(self, exposed_dossier):
        risk = calculate_risk_score(exposed_dossier)
        assert risk.score >= 50
        assert risk.level in ("high", "critical")

    def test_score_is_bounded(self, exposed_dossier):
        risk = calculate_risk_score(exposed_dossier)
        assert 0 <= risk.score <= 100

    def test_has_factors(self, exposed_dossier):
        risk = calculate_risk_score(exposed_dossier)
        assert len(risk.factors) == 6  # social, web, images, emails, professional, confidence

    def test_has_recommendations(self, exposed_dossier):
        risk = calculate_risk_score(exposed_dossier)
        assert len(risk.recommendations) > 0

    def test_factor_scores_sum_to_total(self, exposed_dossier):
        risk = calculate_risk_score(exposed_dossier)
        factor_sum = sum(f["score"] for f in risk.factors)
        assert factor_sum == pytest.approx(risk.score, abs=0.1)

    def test_levels(self):
        for conf, expected_level in [(0.1, "low"), (0.5, "moderate"), (0.8, "high")]:
            d = {
                "confidence_score": conf,
                "social_profiles": [{"platform": "x", "url": "u"}] * 3,
                "web_results": [{"source": "g", "url": "u", "title": "t"}] * 3,
                "image_matches": [],
                "email_addresses": [],
                "professional": [],
            }
            risk = calculate_risk_score(d)
            assert risk.level in ("low", "moderate", "high", "critical")
