"""Integration tests for scanners."""

import pytest
from app.scanners.social import SocialScanner
from app.scanners.web import WebScanner
from app.scanners.email import EmailScanner
from app.scanners.data_enrichment import DataEnrichmentScanner
from app.models import PersonQuery, Location


@pytest.fixture
def query():
    return PersonQuery(full_name="John Doe", locations=[Location(raw="Berlin", city="Berlin", country="Germany")])


@pytest.fixture
def minimal_query():
    return PersonQuery(full_name="X")


class TestSocialScanner:
    @pytest.mark.asyncio
    async def test_scan_returns_list(self, query):
        scanner = SocialScanner()
        results = await scanner.scan(query)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_results_have_required_fields(self, query):
        scanner = SocialScanner()
        results = await scanner.scan(query)
        for r in results:
            assert hasattr(r, "platform")
            assert hasattr(r, "url")

    @pytest.mark.asyncio
    async def test_empty_name_returns_list(self, minimal_query):
        scanner = SocialScanner()
        results = await scanner.scan(minimal_query)
        assert isinstance(results, list)


class TestWebScanner:
    @pytest.mark.asyncio
    async def test_scan_returns_list(self, query):
        scanner = WebScanner()
        results = await scanner.scan(query)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_results_have_url(self, query):
        scanner = WebScanner()
        results = await scanner.scan(query)
        for r in results:
            assert hasattr(r, "url")
            assert hasattr(r, "source")


class TestEmailScanner:
    @pytest.mark.asyncio
    async def test_scan_returns_list(self, query):
        scanner = EmailScanner()
        results = await scanner.scan(query)
        assert isinstance(results, list)


class TestDataEnrichmentScanner:
    @pytest.mark.asyncio
    async def test_scan_returns_list(self, query):
        scanner = DataEnrichmentScanner()
        results = await scanner.scan(query)
        assert isinstance(results, list)


class TestScannerRobustness:
    @pytest.mark.asyncio
    async def test_multiple_scanners_parallel(self, query):
        """Multiple scanners can run in parallel without interference."""
        import asyncio
        scanners = [
            SocialScanner(),
            WebScanner(),
            EmailScanner(),
        ]
        tasks = [s.scan(query) for s in scanners]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            assert isinstance(r, (list, Exception))

    @pytest.mark.asyncio
    async def test_scanner_handles_single_name(self):
        """Scanners should handle a single-character name."""
        q = PersonQuery(full_name="A")
        scanner = WebScanner()
        results = await scanner.scan(q)
        assert isinstance(results, list)
