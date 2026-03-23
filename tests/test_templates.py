"""Tests for search templates module."""

import pytest
from app.templates import get_templates, get_template, get_scanners_for_template, TEMPLATES


class TestGetTemplates:
    def test_returns_list(self):
        templates = get_templates()
        assert isinstance(templates, list)
        assert len(templates) >= 7

    def test_all_have_id_and_name(self):
        for t in get_templates():
            assert "id" in t
            assert "name" in t
            assert "description" in t
            assert "scanners" in t


class TestGetTemplate:
    def test_valid_template(self):
        t = get_template("recruiter")
        assert t is not None
        assert t["name"] == "Recruiter Check"

    def test_invalid_template(self):
        t = get_template("nonexistent")
        assert t is None


class TestGetScannersForTemplate:
    def test_recruiter_scanners(self):
        scanners = get_scanners_for_template("recruiter")
        assert "social" in scanners
        assert "professional" in scanners

    def test_full_osint_scanners(self):
        scanners = get_scanners_for_template("osint_full")
        assert len(scanners) >= 8

    def test_invalid_template_returns_empty(self):
        scanners = get_scanners_for_template("nonexistent")
        assert scanners == []


class TestTemplateConsistency:
    def test_all_templates_have_valid_scanners(self):
        valid_scanners = {"social", "web", "email", "image", "advanced_image",
                         "reverse_image", "deep_social", "professional",
                         "professional_intel", "public_records", "data_enrichment"}
        for tid, template in TEMPLATES.items():
            for scanner in template["scanners"]:
                assert scanner in valid_scanners, f"Template {tid} has invalid scanner: {scanner}"

    def test_all_templates_have_priority(self):
        for tid, template in TEMPLATES.items():
            assert "priority" in template, f"Template {tid} missing priority"
            assert template["priority"] in ("speed", "coverage", "professional", "comprehensive", "web", "email", "images")
