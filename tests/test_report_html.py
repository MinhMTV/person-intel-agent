"""Tests for HTML report generation."""

import pytest
from app.models import PersonQuery, PersonDossier, SocialProfile, SearchResult, ImageMatch, Source, Confidence
from app.report_html import generate_html_report


@pytest.fixture
def sample_dossier():
    query = PersonQuery(full_name="John Smith")
    d = PersonDossier(query=query, confidence_score=0.75)
    d.social_profiles = [
        SocialProfile(platform="github", url="https://github.com/js", username="js", bio="Dev", confidence=Confidence.HIGH, verified=True),
        SocialProfile(platform="twitter", url="https://twitter.com/js", username="js", confidence=Confidence.MEDIUM),
    ]
    d.web_results = [
        SearchResult(source=Source.WEB, title="Blog", url="https://blog.com/js", snippet="About John", confidence=Confidence.HIGH),
    ]
    d.image_matches = [
        ImageMatch(source_url="https://twitter.com/js", image_url="https://img.com/1.jpg", similarity_score=0.92),
    ]
    d.email_addresses = ["john@example.com"]
    return d


class TestHtmlReport:
    def test_generates_valid_html(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html

    def test_contains_name(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "John Smith" in html

    def test_contains_social_profiles(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "github" in html.lower()
        assert "twitter" in html.lower()

    def test_contains_emails(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "john@example.com" in html

    def test_contains_stats(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "75%" in html  # confidence

    def test_contains_image_matches(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "92%" in html

    def test_escaped_content(self):
        query = PersonQuery(full_name='John "Hacker" Smith')
        d = PersonDossier(query=query, confidence_score=0.5)
        d.social_profiles = [
            SocialProfile(platform="github", url="https://github.com/j", username="j<script>", bio="<b>bold</b>"),
        ]
        html = generate_html_report(d, "test")
        assert "<script>" not in html  # Should be escaped
        assert "&lt;script&gt;" in html or "&lt;b&gt;" in html

    def test_empty_dossier(self):
        query = PersonQuery(full_name="Nobody")
        d = PersonDossier(query=query, confidence_score=0.0)
        html = generate_html_report(d, "empty")
        assert "Nobody" in html
        assert "0%" in html

    def test_print_styles(self, sample_dossier):
        html = generate_html_report(sample_dossier, "test123")
        assert "@media print" in html
