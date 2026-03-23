"""Tests for cache module."""

import time
import pytest
import asyncio
from pathlib import Path
from app.cache import ResponseCache, RateLimiter


@pytest.fixture
def cache(tmp_path):
    """Create a cache with temp database."""
    c = ResponseCache(db_path=str(tmp_path / "test_cache.db"), ttl_hours=1)
    yield c
    c.clear_all()


class TestResponseCache:
    def test_set_and_get(self, cache):
        cache.set("https://example.com", "<html>test</html>", 200)
        result = cache.get("https://example.com")

        assert result is not None
        assert result["content"] == "<html>test</html>"
        assert result["status_code"] == 200

    def test_get_missing(self, cache):
        result = cache.get("https://nonexistent.com")
        assert result is None

    def test_different_urls(self, cache):
        cache.set("https://a.com", "a", 200)
        cache.set("https://b.com", "b", 200)

        assert cache.get("https://a.com")["content"] == "a"
        assert cache.get("https://b.com")["content"] == "b"

    def test_overwrite(self, cache):
        cache.set("https://example.com", "old", 200)
        cache.set("https://example.com", "new", 200)
        result = cache.get("https://example.com")
        assert result["content"] == "new"

    def test_clear_all(self, cache):
        cache.set("https://a.com", "a", 200)
        cache.set("https://b.com", "b", 200)
        cache.clear_all()
        assert cache.get("https://a.com") is None
        assert cache.get("https://b.com") is None

    def test_with_headers(self, cache):
        cache.set("https://example.com", "content", 200, headers={"X-Test": "value"})
        result = cache.get("https://example.com")
        assert result["headers"]["X-Test"] == "value"


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_basic_wait(self):
        limiter = RateLimiter(default_delay=0.1)
        start = time.time()
        await limiter.wait("https://example.com/test1")
        await limiter.wait("https://example.com/test2")
        elapsed = time.time() - start
        # Second request should have waited
        assert elapsed >= 0.05

    @pytest.mark.asyncio
    async def test_different_domains_no_wait(self):
        limiter = RateLimiter(default_delay=0.5)
        start = time.time()
        await limiter.wait("https://a.com/path")
        await limiter.wait("https://b.com/path")
        elapsed = time.time() - start
        # Different domains, minimal wait
        assert elapsed < 0.3

    def test_custom_domain_delay(self):
        limiter = RateLimiter(default_delay=1.0)
        limiter.set_domain_delay("api.github.com", 0.5)
        assert limiter._domain_delays["api.github.com"] == 0.5
