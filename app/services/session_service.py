"""Interactive browser login (Playwright) that stores cookies in the SessionStore."""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from typing import Any

from app.infrastructure.security.session_store import PLATFORMS, SessionStore


def ensure_playwright_chromium() -> tuple[bool, str | None]:
    """Install Playwright Chromium on demand (only needed for interactive login)."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "Playwright is not installed (pip install playwright)."
    result = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, "Could not install Playwright Chromium."
    return True, None


async def interactive_login(store: SessionStore, platform: str, timeout_seconds: int = 300) -> dict[str, Any]:
    """Open a visible browser on the local machine, wait for login, keep cookies."""
    config = PLATFORMS.get(platform)
    if config is None:
        return {"success": False, "error": "Unknown platform"}
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "Playwright is not installed."}
    browser = None
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=False)
            except Exception:
                ok, error = ensure_playwright_chromium()
                if not ok:
                    return {"success": False, "error": error}
                browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(config["login_url"], wait_until="domcontentloaded")
            deadline = time.time() + timeout_seconds
            required = [n.lower() for n in config.get("auth_cookie_names", [])]
            while time.time() < deadline:
                await page.wait_for_timeout(1500)
                cookies = await context.cookies()
                names = {str(c.get("name", "")).lower() for c in cookies}
                on_login = any(t in page.url.lower() for t in ("login", "checkpoint", "challenge"))
                if (all(n in names for n in required) if required else not on_login) and not on_login:
                    count = store.save(platform, [dict(c) for c in cookies])
                    return {"success": True, "platform": platform, "cookie_count": count}
            return {"success": False, "error": f"Login timed out after {timeout_seconds}s"}
    except Exception as exc:
        return {"success": False, "error": type(exc).__name__}
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
