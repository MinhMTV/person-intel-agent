"""Tests for base scanner and social scanner utilities."""

import pytest
from app.models import PersonQuery, Location
from app.scanners.base import BaseScanner
from app.scanners.social import SocialScanner


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

    def test_known_platforms(self):
        scanner = SocialScanner()
        assert "github" in scanner.KNOWN_PLATFORMS
        assert "twitter" in scanner.KNOWN_PLATFORMS
        assert "linkedin" in scanner.KNOWN_PLATFORMS
        assert "{}" in scanner.KNOWN_PLATFORMS["github"]
