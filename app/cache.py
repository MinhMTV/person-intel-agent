"""Caching and Rate Limiting Layer.

Features:
  - SQLite-backed response cache
  - Rate limiting per domain
  - Retry logic with exponential backoff
  - Parallel scraping utilities
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import httpx


class ResponseCache:
    """SQLite-backed HTTP response cache.

    Caches HTTP responses to avoid re-fetching the same URLs.
    TTL-based expiration.
    """

    def __init__(self, db_path: str = "output/cache.db", ttl_hours: int = 24):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                url TEXT,
                response TEXT,
                headers TEXT,
                status_code INTEGER,
                created_at TEXT,
                expires_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)")
        conn.commit()
        conn.close()

    def _make_key(self, url: str, method: str = "GET") -> str:
        """Generate cache key from URL."""
        return hashlib.sha256(f"{method}:{url}".encode()).hexdigest()[:16]

    def get(self, url: str, method: str = "GET") -> Optional[dict]:
        """Get cached response for URL."""
        key = self._make_key(url, method)
        try:
            conn = sqlite3.connect(str(self.db_path))
            row = conn.execute(
                "SELECT response, headers, status_code FROM cache WHERE key = ? AND expires_at > ?",
                (key, datetime.utcnow().isoformat()),
            ).fetchone()
            conn.close()

            if row:
                return {
                    "content": row[0],
                    "headers": json.loads(row[1]) if row[1] else {},
                    "status_code": row[2],
                }
        except Exception:
            pass
        return None

    def set(self, url: str, response_text: str, status_code: int,
            headers: dict = None, method: str = "GET"):
        """Cache a response."""
        key = self._make_key(url, method)
        now = datetime.utcnow()
        expires = now + self.ttl

        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                """INSERT OR REPLACE INTO cache (key, url, response, headers, status_code, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (key, url, response_text, json.dumps(headers or {}), status_code,
                 now.isoformat(), expires.isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def clear_expired(self):
        """Remove expired cache entries."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("DELETE FROM cache WHERE expires_at < ?", (datetime.utcnow().isoformat(),))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def clear_all(self):
        """Clear all cache entries."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("DELETE FROM cache")
            conn.commit()
            conn.close()
        except Exception:
            pass


class RateLimiter:
    """Per-domain rate limiter.

    Ensures we don't hit rate limits by spacing out requests
    to the same domain.
    """

    def __init__(self, default_delay: float = 1.0):
        self.default_delay = default_delay
        self._last_request: dict[str, float] = {}
        self._domain_delays: dict[str, float] = {}

    def set_domain_delay(self, domain: str, delay: float):
        """Set custom delay for a specific domain."""
        self._domain_delays[domain] = delay

    async def wait(self, url: str):
        """Wait appropriate time before making request to this domain."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc or url

        delay = self._domain_delays.get(domain, self.default_delay)
        last_time = self._last_request.get(domain, 0)
        elapsed = time.time() - last_time

        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

        self._last_request[domain] = time.time()


class RetryClient:
    """HTTP client with retry logic and caching.

    Wraps httpx.AsyncClient with:
      - Automatic retries with exponential backoff
      - Response caching
      - Rate limiting
    """

    def __init__(
        self,
        cache: Optional[ResponseCache] = None,
        rate_limiter: Optional[RateLimiter] = None,
        max_retries: int = 3,
        timeout: float = 15,
    ):
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.max_retries = max_retries
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "PersonIntelAgent/1.0"},
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def get(self, url: str, use_cache: bool = True, **kwargs) -> Optional[httpx.Response]:
        """GET with retry, cache, and rate limiting."""
        # Check cache
        if use_cache and self.cache:
            cached = self.cache.get(url)
            if cached:
                # Create a mock response
                resp = httpx.Response(
                    status_code=cached["status_code"],
                    content=cached["content"].encode(),
                    headers=cached.get("headers", {}),
                )
                return resp

        # Rate limit
        if self.rate_limiter:
            await self.rate_limiter.wait(url)

        # Retry with exponential backoff
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.get(url, **kwargs)

                # Cache successful responses
                if resp.status_code == 200 and self.cache:
                    self.cache.set(
                        url,
                        resp.text,
                        resp.status_code,
                        dict(resp.headers),
                    )

                return resp

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    await asyncio.sleep(wait_time)
                else:
                    return None

        return None


# Global instances
_default_cache: Optional[ResponseCache] = None
_default_limiter: Optional[RateLimiter] = None


def get_cache() -> ResponseCache:
    """Get or create the global response cache."""
    global _default_cache
    if _default_cache is None:
        _default_cache = ResponseCache()
    return _default_cache


def get_rate_limiter() -> RateLimiter:
    """Get or create the global rate limiter."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter(default_delay=1.0)
        # Custom delays for known rate-limited services
        _default_limiter.set_domain_delay("api.github.com", 0.5)
        _default_limiter.set_domain_delay("scholar.google.com", 3.0)
        _default_limiter.set_domain_delay("www.reddit.com", 2.0)
    return _default_limiter
