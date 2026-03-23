"""API Rate Tracker — track request counts per scanner."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime


class RateTracker:
    """Track API request counts and rates per scanner."""

    def __init__(self):
        self._counts: dict[str, int] = defaultdict(int)
        self._timestamps: dict[str, list[float]] = defaultdict(list)
        self._errors: dict[str, int] = defaultdict(int)
        self._total_time: dict[str, float] = defaultdict(float)

    def record(self, scanner: str, duration_ms: float = 0, error: bool = False):
        """Record a request."""
        self._counts[scanner] += 1
        self._timestamps[scanner].append(time.time())
        self._total_time[scanner] += duration_ms
        if error:
            self._errors[scanner] += 1
        # Keep only last 1000 timestamps per scanner
        if len(self._timestamps[scanner]) > 1000:
            self._timestamps[scanner] = self._timestamps[scanner][-500:]

    def get_stats(self, scanner: str | None = None) -> dict:
        """Get stats for a scanner or all scanners."""
        if scanner:
            return self._scanner_stats(scanner)
        return {
            "scanners": {name: self._scanner_stats(name) for name in self._counts},
            "total_requests": sum(self._counts.values()),
            "total_errors": sum(self._errors.values()),
        }

    def _scanner_stats(self, scanner: str) -> dict:
        ts = self._timestamps.get(scanner, [])
        now = time.time()
        recent = [t for t in ts if now - t < 3600]  # Last hour
        avg_duration = self._total_time[scanner] / max(self._counts[scanner], 1)
        return {
            "total_requests": self._counts[scanner],
            "errors": self._errors[scanner],
            "requests_last_hour": len(recent),
            "avg_duration_ms": round(avg_duration, 1),
            "rpm": round(len(recent) / 60, 2),  # Requests per minute (last hour)
        }

    def reset(self):
        """Reset all counters."""
        self._counts.clear()
        self._timestamps.clear()
        self._errors.clear()
        self._total_time.clear()


# Global instance
tracker = RateTracker()
