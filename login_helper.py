#!/usr/bin/env python3
"""Interactive login helper for LinkedIn, Xing, Instagram.

Run this on the VPS (via VNC) or locally to save login cookies.

Usage:
    python login_helper.py linkedin
    python login_helper.py xing
    python login_helper.py instagram
    python login_helper.py all  # Login to all platforms sequentially
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

SESSIONS_DIR = Path(tempfile.gettempdir()) / "pia_sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

PLATFORMS = {
    "linkedin": {
        "name": "LinkedIn",
        "url": "https://www.linkedin.com/login",
        "check_url": "https://www.linkedin.com/feed/",
    },
    "xing": {
        "name": "Xing",
        "url": "https://www.xing.com/login",
        "check_url": "https://www.xing.com/feed",
    },
    "instagram": {
        "name": "Instagram",
        "url": "https://www.instagram.com/accounts/login/",
        "check_url": "https://www.instagram.com/",
    },
}


async def login_platform(platform: str):
    """Open browser for interactive login."""
    if platform not in PLATFORMS:
        print(f"❌ Unknown platform: {platform}")
        print(f"Available: {', '.join(PLATFORMS.keys())}")
        return

    config = PLATFORMS[platform]

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ Playwright not installed!")
        print("Run: pip install playwright && playwright install chromium")
        return

    print(f"\n{'='*50}")
    print(f"🔑 {config['name']} Login")
    print(f"{'='*50}")
    print(f"Opening browser... (may take a moment)")
    print(f"URL: {config['url']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        try:
            await page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"⚠️ Page load warning: {e}")

        print(f"\n👉 Log in with your {config['name']} credentials")
        print(f"👉 After successful login, press ENTER here to save cookies")
        print(f"   (or type 'cancel' to abort)")

        # Wait for user input
        loop = asyncio.get_event_loop()
        user_input = await loop.run_in_executor(None, input, "\nPress ENTER when logged in: ")

        if user_input.strip().lower() == "cancel":
            print("❌ Cancelled")
            await browser.close()
            return

        # Save cookies
        cookies = await context.cookies()
        session_data = {
            "platform": platform,
            "cookies": cookies,
            "saved_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "cookie_count": len(cookies),
        }

        session_file = SESSIONS_DIR / f"{platform}.json"
        session_file.write_text(json.dumps(session_data, indent=2))

        print(f"\n✅ {config['name']} session saved!")
        print(f"   📁 File: {session_file}")
        print(f"   🍪 Cookies: {len(cookies)}")
        print(f"   🌐 Scanners will now use this session for authenticated searches")

        await browser.close()


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    platform = sys.argv[1].lower()

    if platform == "all":
        for p in PLATFORMS:
            await login_platform(p)
            print()
    else:
        await login_platform(platform)


if __name__ == "__main__":
    asyncio.run(main())
