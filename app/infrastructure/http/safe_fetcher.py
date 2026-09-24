"""Centralised SSRF-safe fetcher for untrusted remote URLs (pages, images).

* http/https only, no credentials in URLs, no internal hostnames
* every resolved IP must be public; in direct mode the TCP connection is made
  to the *validated* IP (DNS-rebinding safe), TLS still uses the hostname (SNI)
* redirects are followed manually and each hop is re-validated
* strict timeouts, byte limits enforced while streaming, MIME allow-lists
* one pooled client for the whole process
"""

from __future__ import annotations

import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpcore
import httpx

from app.config import Settings
from app.infrastructure.security.url_guard import (
    BlockedURLError,
    Resolver,
    parse_public_url,
    resolve_public,
    system_resolver,
)

logger = logging.getLogger(__name__)

CookieProvider = Callable[[str], dict[str, str] | None]

PAGE_TYPES = ("text/html", "application/xhtml+xml")
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/jpg", "image/pjpeg", "application/octet-stream")


class FetchError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code  # blocked | too_large | bad_type | timeout | http_error | redirects | network
        self.message = message
        self.status_code = status_code


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        charset = "utf-8"
        if "charset=" in self.content_type:
            charset = self.content_type.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves + validates at connect time and connects to the validated IP."""

    def __init__(self, resolver: Resolver):
        self._inner = httpcore.AnyIOBackend()
        self._resolver = resolver

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # type: ignore[no-untyped-def]
        addresses = await resolve_public(host, port, self._resolver)
        last_exc: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_exc = exc
        raise httpcore.ConnectError(str(last_exc) if last_exc else "connect failed")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # type: ignore[no-untyped-def]
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _env_proxy_configured() -> bool:
    proxies = urllib.request.getproxies()
    return bool(proxies.get("https") or proxies.get("http"))


class SafeFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cookie_provider: CookieProvider | None = None,
    ):
        self.settings = settings
        self._resolver = resolver or system_resolver
        self._cookie_provider = cookie_provider
        self._client: httpx.AsyncClient | None = None
        self._transport = transport
        self.proxy_mode = transport is None and settings.outbound_use_env_proxy and _env_proxy_configured()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(self.settings.http_timeout_seconds, connect=min(6.0, self.settings.http_timeout_seconds))
            limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
            headers = {
                "User-Agent": self.settings.http_user_agent,
                "Accept-Language": "en;q=0.9,de;q=0.8,*;q=0.5",
            }
            if self._transport is not None:
                transport = self._transport
                trust_env = False
            elif self.proxy_mode:
                transport = None  # httpx builds the proxy transport from the environment
                trust_env = True
            else:
                transport = httpx.AsyncHTTPTransport(limits=limits, trust_env=False)
                # Replace the pool with one whose network backend pins validated IPs.
                transport._pool = httpcore.AsyncConnectionPool(  # noqa: SLF001
                    ssl_context=httpx.create_ssl_context(),
                    max_connections=limits.max_connections,
                    max_keepalive_connections=limits.max_keepalive_connections,
                    keepalive_expiry=limits.keepalive_expiry,
                    network_backend=_GuardedBackend(self._resolver),
                )
                trust_env = False
            self._client = httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                limits=limits,
                headers=headers,
                follow_redirects=False,
                trust_env=trust_env,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _preflight(self, url: str) -> None:
        _, host, port = parse_public_url(url)
        try:
            await resolve_public(host, port, self._resolver)
        except BlockedURLError as exc:
            # Behind an egress proxy the local resolver may not know public
            # hosts; the proxy resolves them. Only an *explicit* private answer blocks.
            if self.proxy_mode and "resolve" in str(exc).lower():
                return
            raise

    async def fetch(self, url: str, *, kind: str = "page", max_bytes: int | None = None) -> FetchResult:
        accept_types = PAGE_TYPES if kind == "page" else IMAGE_TYPES
        if max_bytes is None:
            max_bytes = self.settings.max_remote_page_bytes if kind == "page" else self.settings.max_remote_image_bytes
        accept_header = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5" if kind == "page" else "image/avif,image/webp,image/*;q=0.9"
        client = self._get_client()
        current = url
        for _hop in range(self.settings.http_max_redirects + 1):
            try:
                await self._preflight(current)
            except BlockedURLError as exc:
                raise FetchError("blocked", str(exc)) from exc
            headers = {"Accept": accept_header}
            cookies = self._cookies_for(current)
            if cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            try:
                async with client.stream("GET", current, headers=headers) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError("http_error", "Redirect without Location", resp.status_code)
                        current = urljoin(str(resp.url), location)
                        continue
                    if resp.status_code >= 400:
                        raise FetchError("http_error", f"HTTP {resp.status_code}", resp.status_code)
                    content_type = resp.headers.get("content-type", "").lower()
                    base_type = content_type.split(";")[0].strip()
                    if base_type and not base_type.startswith(accept_types):
                        raise FetchError("bad_type", f"Unexpected content type '{base_type}'")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError("too_large", "Response exceeds size limit")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError("too_large", "Response exceeds size limit")
                        chunks.append(chunk)
                    return FetchResult(
                        url=url,
                        final_url=str(resp.url),
                        status_code=resp.status_code,
                        content_type=content_type,
                        content=b"".join(chunks),
                        headers={k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"},
                    )
            except FetchError:
                raise
            except httpx.TimeoutException as exc:
                raise FetchError("timeout", "Request timed out") from exc
            except (httpx.HTTPError, httpcore.ConnectError, BlockedURLError) as exc:
                if isinstance(exc, BlockedURLError) or isinstance(exc.__cause__, BlockedURLError):
                    raise FetchError("blocked", "Target address is not allowed") from exc
                if "non-public" in str(exc) or "resolve" in str(exc).lower():
                    raise FetchError("blocked", "Target address is not allowed") from exc
                raise FetchError("network", type(exc).__name__) from exc
        raise FetchError("redirects", "Too many redirects")

    def _cookies_for(self, url: str) -> dict[str, str] | None:
        if not self._cookie_provider:
            return None
        try:
            _, host, _ = parse_public_url(url)
        except BlockedURLError:
            return None
        return self._cookie_provider(host)


def create_api_client(settings: Settings) -> httpx.AsyncClient:
    """Pooled client for *configured* provider APIs (Google, TinEye, SearXNG …).

    These endpoints are operator-configured, not user-controlled, so they are
    not subject to the SSRF guard (SearXNG usually runs on localhost).
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds + 8, connect=8.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        headers={"User-Agent": settings.http_user_agent},
        follow_redirects=True,
        trust_env=settings.outbound_use_env_proxy,
    )
