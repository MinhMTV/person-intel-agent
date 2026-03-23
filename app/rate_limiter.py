"""Rate limiter — domain-based request delays to prevent IP bans."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class DomainRateLimiter:
    """Per-domain rate limiting with configurable delays."""

    def __init__(self):
        self._last_request: dict[str, float] = {}
        self._delays: dict[str, float] = {
            # Default delays in seconds between requests to same domain
            "twitter.com": 2.0,
            "linkedin.com": 3.0,
            "instagram.com": 2.0,
            "facebook.com": 3.0,
            "github.com": 1.0,
            "google.com": 1.5,
            "default": 1.0,
        }
        self._request_counts: dict[str, int] = defaultdict(int)

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return "unknown"

    async def wait_if_needed(self, url: str) -> float:
        """Wait if needed before making a request to this domain.
        Returns the actual wait time in seconds.
        """
        domain = self._extract_domain(url)
        delay = self._delays.get(domain, self._delays["default"])

        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        wait_time = max(0, delay - elapsed)

        if wait_time > 0:
            await asyncio.sleep(wait_time)

        self._last_request[domain] = time.time()
        self._request_counts[domain] += 1
        return wait_time

    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "domains_tracked": len(self._last_request),
            "total_requests": sum(self._request_counts.values()),
            "per_domain": {
                domain: {
                    "requests": count,
                    "last_request_ago": round(time.time() - self._last_request.get(domain, 0), 1),
                }
                for domain, count in self._request_counts.items()
            },
        }

    def set_delay(self, domain: str, delay_seconds: float):
        """Set custom delay for a domain."""
        self._delays[domain] = delay_seconds

    def reset(self):
        """Reset all tracking."""
        self._last_request.clear()
        self._request_counts.clear()


# Global instance
limiter = DomainRateLimiter()
