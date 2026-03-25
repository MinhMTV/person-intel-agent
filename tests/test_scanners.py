"""Tests for base scanner and social scanner utilities."""

import asyncio
import pytest
from app.models import PersonQuery, Location, SocialProfile, Confidence
from app.scanners.base import BaseScanner
from app.scanners.social import SocialScanner
from app.scanners.web import WebScanner


class ConcreteScanner(BaseScanner):
    """Concrete implementation for testing."""
    name = "test"
    description = "Test scanner"

    async def scan(self, query):
        return []


class TestBaseScanner:
    def test_name_variants_simple(self):
        scanner = ConcreteScanner()
        q = PersonQuery(full_name="John Smith", first_name="John", last_name="Smith")
        variants = scanner.name_variants(q)

        assert "John Smith" in variants
        assert "Smith, John" in variants
        assert "Smith John" in variants
        assert '"John Smith"' in variants

    def test_name_variants_with_nicknames(self):
        scanner = ConcreteScanner()
        q = PersonQuery(
            full_name="John Smith",
            first_name="John",
            last_name="Smith",
            nicknames=["Johnny", "Jon"],
        )
        variants = scanner.name_variants(q)

        assert "Johnny Smith" in variants
        assert "Jon Smith" in variants
        assert "Johnny" in variants

    def test_location_queries(self):
        scanner = ConcreteScanner()
        q = PersonQuery(
            full_name="John Smith",
            first_name="John",
            last_name="Smith",
            locations=[
                Location(raw="Berlin", city="Berlin", state="Berlin"),
            ],
        )
        queries = scanner.location_queries(q)

        assert any("Berlin" in q for q in queries)
        # Should have name + location combos
        assert len(queries) > 0

    def test_name_variants_three_part_name(self):
        scanner = ConcreteScanner()
        q = PersonQuery(full_name="Minh Tuan Vuong", first_name="Minh", last_name="Vuong")
        variants = scanner.name_variants(q)

        assert "Minh Vuong" in variants
        assert "Tuan Vuong" in variants
        assert '"Minh Vuong"' in variants


class TestSocialScanner:
    def test_generate_usernames(self):
        scanner = SocialScanner()
        q = PersonQuery(
            full_name="John Smith",
            first_name="John",
            last_name="Smith",
            usernames=["existing_user"],
        )
        usernames = scanner._generate_usernames(q)

        assert "existing_user" in usernames
        assert "johnsmith" in usernames
        assert "john.smith" in usernames
        assert "john_smith" in usernames
        assert "jsmith" in usernames

    def test_generate_usernames_with_nicknames(self):
        scanner = SocialScanner()
        q = PersonQuery(
            full_name="John Smith",
            first_name="John",
            last_name="Smith",
            nicknames=["Johnny"],
        )
        usernames = scanner._generate_usernames(q)

        assert "johnny" in usernames
        assert "johnnysmith" in usernames
        assert "johnny.smith" in usernames

    def test_generate_usernames_no_duplicates(self):
        scanner = SocialScanner()
        q = PersonQuery(
            full_name="John Smith",
            first_name="John",
            last_name="Smith",
        )
        usernames = scanner._generate_usernames(q)

        # Should not have duplicates
        assert len(usernames) == len(set(usernames))

    def test_generate_usernames_three_part_name(self):
        scanner = SocialScanner()
        q = PersonQuery(
            full_name="Minh Tuan Vuong",
            first_name="Minh",
            last_name="Vuong",
        )
        usernames = scanner._generate_usernames(q)

        assert "minhtv" in usernames
        assert "minhmtv" in usernames
        assert "minhtuanvuong" in usernames
        assert any(name.startswith("minh") for name in usernames)

    def test_scan_checks_deeper_username_candidates(self, monkeypatch):
        scanner = SocialScanner()
        q = PersonQuery(
            full_name="Minh Tuan Vuong",
            first_name="Minh",
            last_name="Vuong",
            include_platforms=["github", "instagram", "linkedin"],
        )

        seen_usernames = []

        async def fake_check(username, selected_platforms=None):
            seen_usernames.append(username)
            if username == "minhtv":
                return [SocialProfile(platform="linkedin", url="https://www.linkedin.com/in/minhtv/", username=username, confidence=Confidence.HIGH)]
            if username == "minhmtv":
                return [SocialProfile(platform="github", url="https://github.com/MinhMTV/", username=username, confidence=Confidence.HIGH)]
            return []

        monkeypatch.setattr(scanner, "_check_username", fake_check)
        results = asyncio.run(scanner.scan(q))

        urls = {r.url for r in results}
        assert "minhtv" in seen_usernames
        assert "minhmtv" in seen_usernames
        assert "https://www.linkedin.com/in/minhtv/" in urls
        assert "https://github.com/MinhMTV/" in urls

    def test_extract_profile_image_from_og_meta(self):
        scanner = SocialScanner()
        html = """
        <html><head><meta property="og:image" content="https://cdn.example.com/avatar.jpg"></head><body></body></html>
        """

        image_url = asyncio.run(scanner._extract_profile_image(None, "instagram", html, "https://instagram.com/MinhMTV"))
        assert image_url == "https://cdn.example.com/avatar.jpg"


class TestWebScanner:
    def test_build_queries_for_three_part_name(self):
        scanner = WebScanner()
        q = PersonQuery(
            full_name="Minh Tuan Vuong",
            first_name="Minh",
            last_name="Vuong",
            include_platforms=["linkedin", "xing", "github", "facebook", "instagram"],
            locations=[Location(raw="Vienna", city="Vienna", country="Austria", country_code="AT")],
        )

        queries = scanner._build_search_queries(q)
        query_text = "\n".join(queries)

        assert '"Minh Tuan Vuong" site:linkedin.com' in query_text
        assert '"Minh Vuong" site:xing.com' in query_text
        assert '"Tuan Vuong" site:github.com' in query_text
        assert '"Vuong, Minh" site:facebook.com' in query_text

    def test_known_platforms(self):
        scanner = SocialScanner()
        assert "github" in scanner.KNOWN_PLATFORMS
        assert "twitter" in scanner.KNOWN_PLATFORMS
        assert "linkedin" in scanner.KNOWN_PLATFORMS
        assert "{}" in scanner.KNOWN_PLATFORMS["github"]
