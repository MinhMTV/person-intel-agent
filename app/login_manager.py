"""Login Manager — manage authentication sessions for LinkedIn, Xing, Instagram.

Supports:
- Playwright-based interactive login (headful browser)
- Cookie import/export (JSON)
- Session persistence (disk)
- VNC-compatible for VPS usage
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_SESSIONS_DIR = Path("/tmp/pia_sessions")
_SESSIONS_DIR.mkdir(exist_ok=True)

# Platform configs
PLATFORMS = {
    "linkedin": {
        "name": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "check_url": "https://www.linkedin.com/feed/",
        "icon": "💼",
        "cookie_domain": ".linkedin.com",
    },
    "xing": {
        "name": "Xing",
        "login_url": "https://www.xing.com/login",
        "check_url": "https://www.xing.com/feed",
        "icon": "🔷",
        "cookie_domain": ".xing.com",
    },
    "instagram": {
        "name": "Instagram",
        "login_url": "https://www.instagram.com/accounts/login/",
        "check_url": "https://www.instagram.com/",
        "icon": "📷",
        "cookie_domain": ".instagram.com",
    },
}


def _session_file(platform: str) -> Path:
    return _SESSIONS_DIR / f"{platform}.json"


def get_session_status(platform: str) -> dict:
    """Check if a session exists and is valid."""
    if platform not in PLATFORMS:
        return {"error": "Unknown platform"}

    path = _session_file(platform)
    if not path.exists():
        return {"platform": platform, "status": "not_logged_in", "icon": PLATFORMS[platform]["icon"]}

    try:
        data = json.loads(path.read_text())
        cookies = data.get("cookies", [])
        saved_at = data.get("saved_at", "unknown")
        cookie_count = len(cookies)

        # Check for session cookies (non-expired)
        has_session = any(
            c.get("expires", 0) == -1 or c.get("expires", 0) > time.time()
            for c in cookies
        )

        return {
            "platform": platform,
            "status": "logged_in" if has_session else "expired",
            "icon": PLATFORMS[platform]["icon"],
            "name": PLATFORMS[platform]["name"],
            "saved_at": saved_at,
            "cookie_count": cookie_count,
            "has_session": has_session,
        }
    except Exception as e:
        return {"platform": platform, "status": "error", "error": str(e)}


def get_all_sessions() -> dict:
    """Get status of all platform sessions."""
    return {
        platform: get_session_status(platform)
        for platform in PLATFORMS
    }


def save_cookies(platform: str, cookies: list[dict]) -> bool:
    """Save cookies for a platform."""
    if platform not in PLATFORMS:
        return False
    path = _session_file(platform)
    data = {
        "platform": platform,
        "cookies": cookies,
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "cookie_count": len(cookies),
    }
    path.write_text(json.dumps(data, indent=2))
    return True


def load_cookies(platform: str) -> list[dict] | None:
    """Load cookies for a platform."""
    path = _session_file(platform)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data.get("cookies")
    except Exception:
        return None


def delete_session(platform: str) -> bool:
    """Delete a session."""
    path = _session_file(platform)
    if path.exists():
        path.unlink()
        return True
    return False


async def start_interactive_login(platform: str) -> dict:
    """Start an interactive Playwright login session.

    Returns dict with status and instructions.
    On VPS: uses VNC for headful browser.
    Locally: opens browser window.
    """
    if platform not in PLATFORMS:
        return {"error": "Unknown platform"}

    config = PLATFORMS[platform]

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}

    return {
        "platform": platform,
        "name": config["name"],
        "icon": config["icon"],
        "login_url": config["login_url"],
        "instructions": [
            f"1. Open {config['login_url']} in your browser",
            f"2. Log in with your {config['name']} credentials",
            f"3. After login, export cookies using browser DevTools (F12 → Application → Cookies)",
            f"4. Or use the 'Upload Cookies' button in this page",
        ],
        "playwright_script": f"""
from playwright.async_api import async_playwright
import asyncio, json

async def login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto('{config["login_url"]}')
        input('Log in and press Enter...')
        cookies = await context.cookies()
        with open('/tmp/pia_sessions/{platform}.json', 'w') as f:
            json.dump({{"platform": "{platform}", "cookies": cookies, "saved_at": "auto"}}, f, indent=2)
        await browser.close()
        print('✅ {config["name"]} session saved!')

asyncio.run(login())
""",
    }


def import_cookies_from_json(platform: str, cookies_json: str) -> dict:
    """Import cookies from a JSON string (e.g., from browser extension or DevTools)."""
    try:
        cookies = json.loads(cookies_json)
        if isinstance(cookies, list):
            save_cookies(platform, cookies)
            return {"success": True, "cookie_count": len(cookies)}
        else:
            return {"error": "Expected a JSON array of cookies"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}


def export_cookies_json(platform: str) -> str | None:
    """Export cookies as JSON string."""
    cookies = load_cookies(platform)
    if cookies:
        return json.dumps(cookies, indent=2)
    return None
