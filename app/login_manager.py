"""Login Manager — manage authentication sessions for social/professional sites.

Supports:
- Playwright-based interactive login (headful browser)
- Cookie import/export (JSON)
- Session persistence (disk)
- VNC-compatible for VPS usage
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

_SESSIONS_DIR = Path("/tmp/pia_sessions")
_SESSIONS_DIR.mkdir(exist_ok=True)
_PLAYWRIGHT_MARKER_DIR = _SESSIONS_DIR / "playwright"
_PLAYWRIGHT_MARKER_DIR.mkdir(exist_ok=True)

# Platform configs
PLATFORMS = {
    "linkedin": {
        "name": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "check_url": "https://www.linkedin.com/feed/",
        "icon": "💼",
        "cookie_domain": ".linkedin.com",
        "auth_cookie_names": ["li_at"],
    },
    "xing": {
        "name": "Xing",
        "login_url": "https://login.xing.com/",
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
        "auth_cookie_names": ["sessionid"],
        "min_login_seconds": 8,
    },
    "facebook": {
        "name": "Facebook",
        "login_url": "https://www.facebook.com/login",
        "check_url": "https://www.facebook.com/",
        "icon": "📘",
        "cookie_domain": ".facebook.com",
        "auth_cookie_names": ["c_user"],
        "min_login_seconds": 8,
    },
}


def _session_file(platform: str) -> Path:
    return _SESSIONS_DIR / f"{platform}.json"


def _platform_config(platform: str) -> dict[str, Any] | None:
    return PLATFORMS.get(platform)


def _matching_cookies(cookies: list[dict], cookie_domain: str) -> list[dict]:
    """Return cookies relevant for the requested platform."""
    normalized = cookie_domain.lstrip(".")
    matches = []
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".")
        if domain == normalized or domain.endswith(f".{normalized}"):
            matches.append(cookie)
    return matches


def _has_auth_cookie(cookies: list[dict], cookie_names: list[str] | None) -> bool:
    """Check whether cookies contain a likely authenticated session cookie."""
    if not cookie_names:
        return bool(cookies)
    names = {str(cookie.get("name", "")).lower() for cookie in cookies}
    return any(name.lower() in names for name in cookie_names)


def _playwright_marker(browser: str = "chromium") -> Path:
    try:
        pw_version = version("playwright")
    except PackageNotFoundError:
        pw_version = "missing"
    return _PLAYWRIGHT_MARKER_DIR / f"{browser}-{pw_version}.ok"


def ensure_playwright_browser(browser: str = "chromium", force: bool = False) -> dict:
    """Ensure the requested Playwright browser binary is installed."""
    marker = _playwright_marker(browser)
    if marker.exists() and not force:
        return {"success": True, "browser": browser, "cached": True}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", browser],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        return {"success": False, "browser": browser, "error": str(e)}

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "").strip()
        return {"success": False, "browser": browser, "error": error or f"Failed to install {browser}"}

    marker.write_text(datetime.utcnow().isoformat() + "Z")
    return {"success": True, "browser": browser, "installed": True}


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
        platform_cookies = _matching_cookies(cookies, PLATFORMS[platform]["cookie_domain"])
        has_auth_cookie = _has_auth_cookie(platform_cookies, PLATFORMS[platform].get("auth_cookie_names"))

        # Check for session cookies (non-expired)
        has_session = any(
            c.get("expires", 0) == -1 or c.get("expires", 0) > time.time()
            for c in platform_cookies
        )

        return {
            "platform": platform,
            "status": "logged_in" if has_session and has_auth_cookie else "expired",
            "icon": PLATFORMS[platform]["icon"],
            "name": PLATFORMS[platform]["name"],
            "saved_at": saved_at,
            "cookie_count": cookie_count,
            "has_session": has_session,
            "has_auth_cookie": has_auth_cookie,
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


async def verify_session(platform: str) -> dict:
    """Verify that saved cookies still produce an authenticated session."""
    config = _platform_config(platform)
    if not config:
        return {"platform": platform, "success": False, "error": "Unknown platform"}

    cookies = load_cookies(platform)
    if not cookies:
        return {"platform": platform, "success": False, "status": "not_logged_in", "error": "No saved cookies"}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"platform": platform, "success": False, "error": "Playwright is not installed in this environment."}

    ensure_result = ensure_playwright_browser("chromium")
    if not ensure_result.get("success"):
        return {"platform": platform, "success": False, "error": ensure_result.get("error", "Chromium install failed")}

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto(config["check_url"], wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            current_url = page.url.lower()
            on_login_page = any(token in current_url for token in ("login", "checkpoint", "challenge"))
            current_cookies = await context.cookies()
            platform_cookies = _matching_cookies(current_cookies, config["cookie_domain"])
            has_auth_cookie = _has_auth_cookie(platform_cookies, config.get("auth_cookie_names"))
            success = has_auth_cookie and not on_login_page
            return {
                "platform": platform,
                "success": success,
                "status": "logged_in" if success else "expired",
                "checked_url": page.url,
                "cookie_count": len(platform_cookies),
            }
    except Exception as e:
        return {"platform": platform, "success": False, "status": "error", "error": str(e)}
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


def delete_session(platform: str) -> bool:
    """Delete a session."""
    path = _session_file(platform)
    if path.exists():
        path.unlink()
        return True
    return False


def get_login_instructions(platform: str) -> dict:
    """Return login instructions for a supported platform."""
    config = _platform_config(platform)
    if not config:
        return {"error": "Unknown platform"}

    try:
        import playwright  # noqa: F401
    except ImportError:
        return {"error": "Playwright is not installed in this environment."}

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


async def start_interactive_login(platform: str, timeout_seconds: int = 300) -> dict:
    """Open a visible browser, wait for login, and save cookies automatically."""
    config = _platform_config(platform)
    if not config:
        return {"platform": platform, "success": False, "error": "Unknown platform"}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "platform": platform,
            "success": False,
            "error": "Playwright is not installed in this environment.",
        }

    ensure_result = ensure_playwright_browser("chromium")
    if not ensure_result.get("success"):
        return {
            "platform": platform,
            "success": False,
            "name": config["name"],
            "error": f"Could not install Playwright Chromium automatically: {ensure_result['error']}",
        }

    browser = None
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=False)
            except Exception as e:
                if "Executable doesn't exist" in str(e):
                    retry_result = ensure_playwright_browser("chromium", force=True)
                    if not retry_result.get("success"):
                        return {
                            "platform": platform,
                            "success": False,
                            "name": config["name"],
                            "error": f"Chromium is missing and auto-install failed: {retry_result['error']}",
                        }
                    browser = await p.chromium.launch(headless=False)
                else:
                    raise
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(config["login_url"], wait_until="domcontentloaded")

            deadline = time.time() + timeout_seconds
            min_login_time = time.time() + config.get("min_login_seconds", 0)
            while time.time() < deadline:
                await page.wait_for_timeout(1500)
                cookies = await context.cookies()
                platform_cookies = _matching_cookies(cookies, config["cookie_domain"])
                current_url = page.url
                on_login_page = any(token in current_url.lower() for token in ("login", "checkpoint", "challenge"))
                reached_target = current_url.startswith(config["check_url"])
                has_auth_cookie = _has_auth_cookie(platform_cookies, config.get("auth_cookie_names"))

                if time.time() >= min_login_time and has_auth_cookie and (reached_target or not on_login_page):
                    save_cookies(platform, platform_cookies)
                    return {
                        "platform": platform,
                        "success": True,
                        "name": config["name"],
                        "cookie_count": len(platform_cookies),
                        "saved_at": datetime.utcnow().isoformat() + "Z",
                    }

            return {
                "platform": platform,
                "success": False,
                "name": config["name"],
                "error": f"Login timed out after {timeout_seconds} seconds",
            }
    except Exception as e:
        return {"platform": platform, "success": False, "name": config["name"], "error": str(e)}
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


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
