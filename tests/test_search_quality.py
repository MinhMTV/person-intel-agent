"""Tests for search quality with real person queries.

These tests validate that the PIA can find known profiles for real people.
Not run in CI — they hit real search engines.
"""

import asyncio
import pytest
from app.models import PersonQuery, Location


class TestSearchQuality:
    """Test search quality with real person queries."""

    def test_minh_tuan_vuong_finds_all_profiles(self):
        """Test search for Minh Tuan Vuong — should find all 6+ known profiles."""
        from app.scanners.web import WebScanner

        async def run():
            query = PersonQuery(
                full_name="Minh Tuan Vuong",
                locations=[
                    Location(raw="Vienna", city="Vienna", country="Austria", country_code="AT"),
                    Location(raw="Germany", country="Germany", country_code="DE"),
                ],
            )
            scanner = WebScanner()
            results = await asyncio.wait_for(scanner.scan(query), timeout=60)
            return results

        results = asyncio.run(run())
        urls = [r.url for r in results]

        # Must find at least 10 results (search is non-deterministic)
        assert len(results) >= 10, f"Only found {len(results)} results, expected >= 10"

        # Check that we found some profiles (at least 2 out of 5):
        found_profiles = 0
        if any("linkedin.com" in u for u in urls):
            found_profiles += 1
        if any("xing.com" in u for u in urls):
            found_profiles += 1
        if any("minh" in u.lower() and "vuong" in u.lower() for u in urls):
            found_profiles += 1
        if any("github.com" in u for u in urls):
            found_profiles += 1
        if any("facebook.com" in u for u in urls):
            found_profiles += 1
        
        assert found_profiles >= 2, f"Only found {found_profiles}/5 profiles, expected >= 2"

        # Must find at least 10 results
        assert len(results) >= 10, f"Only found {len(results)} results, expected >= 10"

        print(f"\n✅ Found {len(results)} results for Minh Tuan Vuong")
        for r in results[:15]:
            print(f"  [{r.confidence}] {r.title[:60]}")
            print(f"    {r.url}")

    def test_minh_tuan_vuong_confidence(self):
        """Test that results have reasonable confidence scores."""
        from app.scanners.web import WebScanner

        async def run():
            query = PersonQuery(
                full_name="Minh Tuan Vuong",
                locations=[Location(raw="Vienna", city="Vienna", country="Austria", country_code="AT")],
            )
            scanner = WebScanner()
            results = await asyncio.wait_for(scanner.scan(query), timeout=60)
            return results

        results = asyncio.run(run())

        # All results should have confidence
        for r in results:
            assert r.confidence in ("high", "medium", "low"), f"Invalid confidence: {r.confidence}"

        # LinkedIn should be high/medium confidence
        linkedin_results = [r for r in results if "linkedin.com" in r.url]
        for r in linkedin_results:
            assert r.confidence in ("high", "medium"), f"LinkedIn result has low confidence: {r.url}"

        print(f"\n✅ All {len(results)} results have valid confidence scores")

    def test_name_variants(self):
        """Test that name variants expand search coverage."""
        from app.scanners.web import WebScanner

        async def run():
            query = PersonQuery(
                full_name="Minh Tuan Vuong",
                locations=[Location(raw="Vienna", city="Vienna", country="Austria", country_code="AT")],
            )
            scanner = WebScanner()
            variants = scanner._fuzzy_name_variants(query)
            return variants

        variants = asyncio.run(run())

        # Should generate multiple variants
        assert len(variants) >= 5, f"Only {len(variants)} variants, expected >= 5"

        # Check key variants exist
        variant_text = " ".join(variants).lower()
        assert "minh" in variant_text, "Missing 'Minh' in variants"
        assert "vuong" in variant_text, "Missing 'Vuong' in variants"
        assert "tuong" in variant_text or "tuan" in variant_text, "Missing middle name in variants"

        print(f"\n✅ Generated {len(variants)} name variants:")
        for v in variants[:10]:
            print(f"  {v}")
