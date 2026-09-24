"""Browser-session cookie store (LinkedIn, Xing, …).

Security properties:
* cookies are held in memory by default; disk persistence is opt-in
  (``SESSION_PERSISTENCE_ENABLED``) and encrypted with Fernet when
  ``SESSION_ENCRYPTION_KEY`` is configured (files are always 0600)
* cookie *values* are never returned by any API, export or log line
* cookies are only attached to requests whose host belongs to the
  platform's cookie domain
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

PLATFORMS: dict[str, dict[str, Any]] = {
    "linkedin": {
        "name": "LinkedIn",
        "login_url": "https://www.linkedin.com/login",
        "check_url": "https://www.linkedin.com/feed/",
        "cookie_domain": "linkedin.com",
        "auth_cookie_names": ["li_at"],
    },
    "xing": {
        "name": "Xing",
        "login_url": "https://login.xing.com/",
        "check_url": "https://www.xing.com/feed",
        "cookie_domain": "xing.com",
        "auth_cookie_names": [],
    },
    "instagram": {
        "name": "Instagram",
        "login_url": "https://www.instagram.com/accounts/login/",
        "check_url": "https://www.instagram.com/",
        "cookie_domain": "instagram.com",
        "auth_cookie_names": ["sessionid"],
    },
    "facebook": {
        "name": "Facebook",
        "login_url": "https://www.facebook.com/login",
        "check_url": "https://www.facebook.com/",
        "cookie_domain": "facebook.com",
        "auth_cookie_names": ["c_user"],
    },
}


def _domain_matches(host: str, cookie_domain: str) -> bool:
    host = host.lower().lstrip(".")
    cookie_domain = cookie_domain.lower().lstrip(".")
    return host == cookie_domain or host.endswith("." + cookie_domain)


class SessionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._fernet = None
        if settings.session_encryption_key:
            try:
                from cryptography.fernet import Fernet

                self._fernet = Fernet(settings.session_encryption_key.get_secret_value().encode())
            except Exception:
                logger.error("SESSION_ENCRYPTION_KEY is invalid; session persistence disabled")
        if self.persistent:
            self._load_all()

    @property
    def persistent(self) -> bool:
        return self.settings.session_persistence_enabled

    @property
    def encrypted(self) -> bool:
        return self._fernet is not None

    def _file(self, platform: str) -> Path:
        return self.settings.session_dir / (f"{platform}.session" if self.encrypted else f"{platform}.json")

    def _load_all(self) -> None:
        for platform in PLATFORMS:
            path = self._file(platform)
            if not path.exists():
                continue
            try:
                raw = path.read_bytes()
                if self._fernet:
                    raw = self._fernet.decrypt(raw)
                self._sessions[platform] = json.loads(raw)
            except Exception:
                logger.warning("Could not load stored session for %s", platform)

    def _persist(self, platform: str) -> None:
        if not self.persistent:
            return
        if not self.encrypted:
            logger.warning("Persisting %s session WITHOUT encryption (set SESSION_ENCRYPTION_KEY)", platform)
        path = self._file(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self._sessions[platform]).encode()
        if self._fernet:
            data = self._fernet.encrypt(data)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_bytes(data)

    def save(self, platform: str, cookies: list[dict[str, Any]]) -> int:
        if platform not in PLATFORMS:
            raise ValueError("Unknown platform")
        domain = PLATFORMS[platform]["cookie_domain"]
        relevant = [
            {k: c.get(k) for k in ("name", "value", "domain", "path", "expires", "secure", "httpOnly")}
            for c in cookies
            if isinstance(c, dict) and c.get("name") and _domain_matches(str(c.get("domain", domain)), domain)
        ]
        if not relevant:
            raise ValueError(f"No cookies for {domain} found")
        with self._lock:
            self._sessions[platform] = {"cookies": relevant, "saved_at": datetime.now(UTC).isoformat()}
            self._persist(platform)
        return len(relevant)

    def delete(self, platform: str) -> bool:
        with self._lock:
            existed = self._sessions.pop(platform, None) is not None
            for path in (self.settings.session_dir / f"{platform}.session", self.settings.session_dir / f"{platform}.json"):
                if path.exists():
                    path.unlink()
                    existed = True
        return existed

    def status(self, platform: str) -> dict[str, Any]:
        """Session status WITHOUT any cookie names/values."""
        config = PLATFORMS[platform]
        session = self._sessions.get(platform)
        base = {"platform": platform, "name": config["name"], "persistent": self.persistent, "encrypted": self.encrypted}
        if not session:
            return {**base, "status": "not_logged_in"}
        cookies = session["cookies"]
        now = time.time()
        live = [c for c in cookies if not c.get("expires") or c["expires"] == -1 or c["expires"] > now]
        names = {str(c.get("name", "")).lower() for c in live}
        required = [n.lower() for n in config.get("auth_cookie_names", [])]
        has_auth = all(n in names for n in required) if required else bool(live)
        return {
            **base,
            "status": "logged_in" if has_auth else "expired",
            "saved_at": session.get("saved_at"),
            "cookie_count": len(cookies),
        }

    def all_status(self) -> list[dict[str, Any]]:
        return [self.status(p) for p in PLATFORMS]

    def cookies_for_host(self, host: str) -> dict[str, str] | None:
        """Cookie jar for a request host (used by the SafeFetcher)."""
        for platform, config in PLATFORMS.items():
            if _domain_matches(host, config["cookie_domain"]) and platform in self._sessions:
                now = time.time()
                return {
                    c["name"]: c["value"]
                    for c in self._sessions[platform]["cookies"]
                    if c.get("value") is not None and (not c.get("expires") or c["expires"] == -1 or c["expires"] > now)
                }
        return None
