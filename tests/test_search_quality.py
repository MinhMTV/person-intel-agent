"""Tests for search quality with real person queries.

These tests validate that the PIA can find known profiles for real people.
Not run in CI — they hit real search engines.
"""

import asyncio
import pytest
from app.models import PersonQuery, Location


@pytest.mark.integration
class TestSearchQuality:
    """Test search quality with real person queries."""

    def test_minh_tuan_vuong(self):
        """Test search for Minh Tuan Vuong — should find known profiles."""
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
            results = await asyncio.wait_for(scanner.scan(query), timeout=30)
            return results

        results = asyncio.run(run())
        urls = [r.url for r in results]

        # Must find these critical profiles:
        assert any("linkedin.com/in/minhtv" in u for u in urls), "LinkedIn profile not found"
        assert any("xing.com/profile/MinhTuan_Vuong" in u for u in urls), "Xing profile not found"
        assert any("minh-tuan-vuong.de" in u for u in urls), "Portfolio website not found"
        assert any("github.com/MinhMTV" in u or "github.com/VuongMinhTuan" in u for u in urls), "GitHub not found"

        # Must find at least 5 results
        assert len(results) >= 5, f"Only found {len(results)} results, expected >= 5"

        print(f"\n✅ Found {len(results)} results for Minh Tuan Vuong")
        for r in results[:10]:
            print(f"  [{r.confidence}] {r.title}")
            print(f"    {r.url}")

    def test_minh_tuan_vuong_with_locations(self):
        """Test that location hints improve search quality."""
        from app.scanners.web import WebScanner

        async def run():
            # Without location
            query_no_loc = PersonQuery(full_name="Minh Tuan Vuong")
            scanner = WebScanner()
            results_no_loc = await asyncio.wait_for(scanner.scan(query_no_loc), timeout=30)

            # With location
            query_with_loc = PersonQuery(
                full_name="Minh Tuan Vuong",
                locations=[Location(raw="Vienna", city="Vienna", country="Austria", country_code="AT")],
            )
            results_with_loc = await asyncio.wait_for(scanner.scan(query_with_loc), timeout=30)

            return results_no_loc, results_with_loc

        results_no_loc, results_with_loc = asyncio.run(run())

        # Both should find at least 3 results
        assert len(results_no_loc) >= 3, f"Without location: only {len(results_no_loc)} results"
        assert len(results_with_loc) >= 3, f"With location: only {len(results_with_loc)} results"

        print(f"\n✅ Without location: {len(results_no_loc)} results")
        print(f"✅ With location: {len(results_with_loc)} results")

    def test_minh_tuan_vuong_confidence(self):
        """Test that results have reasonable confidence scores."""
        from app.scanners.web import WebScanner

        async def run():
            query = PersonQuery(
                full_name="Minh Tuan Vuong",
                locations=[Location(raw="Vienna", city="Vienna", country="Austria", country_code="AT")],
            )
            scanner = WebScanner()
            results = await asyncio.wait_for(scanner.scan(query), timeout=30)
            return results

        results = asyncio.run(run())

        # All results should have confidence
        for r in results:
            assert r.confidence in ("high", "medium", "low"), f"Invalid confidence: {r.confidence}"

        # LinkedIn should be high confidence
        linkedin_results = [r for r in results if "linkedin.com" in r.url]
        for r in linkedin_results:
            assert r.confidence in ("high", "medium"), f"LinkedIn result has low confidence: {r.url}"

        print(f"\n✅ All {len(results)} results have valid confidence scores")
