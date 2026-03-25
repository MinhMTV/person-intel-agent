"""Helpers for detecting whether Playwright can run in this process."""

from __future__ import annotations

import asyncio
import os


def playwright_runtime_issue() -> str | None:
    """Return a human-readable reason when Playwright is not usable."""
    if os.name != "nt":
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    if "proactor" in loop.__class__.__name__.lower():
        return None

    return (
        "Playwright is not available on the current Windows event loop. "
        "Use a Proactor loop or disable Playwright-based scanners."
    )


def can_use_playwright() -> bool:
    """True when the current process can safely launch Playwright subprocesses."""
    return playwright_runtime_issue() is None
