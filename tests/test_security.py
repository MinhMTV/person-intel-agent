import httpx
import pytest

from app.infrastructure.http.safe_fetcher import FetchError, SafeFetcher
from app.infrastructure.security.redaction import redact
from app.infrastructure.security.session_store import SessionStore
from app.infrastructure.security.url_guard import BlockedURLError, is_ip_allowed, parse_public_url, resolve_public
from tests.conftest import make_settings
from tests.fakes import WebWorld, make_image, public_resolver


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "::1",
                                "fe80::1", "fd00::1", "::ffff:127.0.0.1", "0.0.0.0", "100.64.0.1", "fd00:ec2::254"])
def test_blocks_private_ips(ip):
    assert not is_ip_allowed(ip)


def test_allows_public_ips():
    assert is_ip_allowed("93.184.216.34") and is_ip_allowed("2606:4700:4700::1111")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x", "http://localhost/x", "http://127.0.0.1/",
                                 "http://user:pw@example.com/", "http://metadata.google.internal/", "http://2130706433/",
                                 "http://[::1]/", "ftp://example.com/"])
def test_rejects_dangerous_urls(url):
    with pytest.raises(BlockedURLError):
        parse_public_url(url)


async def test_resolution_to_private_address_is_blocked():
    with pytest.raises(BlockedURLError):
        await resolve_public("internal.example.com", 80, public_resolver)
    assert await resolve_public("example.com", 80, public_resolver) == ["93.184.216.34"]


def fetcher(tmp_path, world: WebWorld, **kw) -> SafeFetcher:
    return SafeFetcher(make_settings(tmp_path, **kw), resolver=public_resolver, transport=httpx.MockTransport(world.handler))


async def test_fetcher_blocks_redirect_to_private_network(tmp_path):
    world = WebWorld()
    world.redirects["https://example.com/a"] = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(FetchError) as e:
        await fetcher(tmp_path, world).fetch("https://example.com/a")
    assert e.value.code == "blocked"
    assert "169.254.169.254" not in " ".join(world.requests)


async def test_fetcher_blocks_dns_to_private(tmp_path):
    world = WebWorld()
    with pytest.raises(FetchError) as e:
        await fetcher(tmp_path, world).fetch("https://internal.example.com/")
    assert e.value.code == "blocked" and not world.requests


async def test_fetcher_limits_redirects_size_and_types(tmp_path):
    world = WebWorld()
    for i in range(10):
        world.redirects[f"https://example.com/{i}"] = f"https://example.com/{i + 1}"
    with pytest.raises(FetchError) as e:
        await fetcher(tmp_path, world).fetch("https://example.com/0")
    assert e.value.code == "redirects"
    world.image("https://example.com/big.png", b"x" * 5000)
    with pytest.raises(FetchError) as e:
        await fetcher(tmp_path, world).fetch("https://example.com/big.png", kind="image", max_bytes=1000)
    assert e.value.code == "too_large"
    world.page("https://example.com/page", "<html></html>")
    with pytest.raises(FetchError) as e:
        await fetcher(tmp_path, world).fetch("https://example.com/page", kind="image")
    assert e.value.code == "bad_type"
    world.image("https://example.com/ok.png", make_image(seed=1))
    ok = await fetcher(tmp_path, world).fetch("https://example.com/ok.png", kind="image")
    assert ok.content.startswith(b"\x89PNG")


async def test_guarded_backend_pins_validated_ips(tmp_path):
    """Direct mode: the connection layer itself refuses private resolutions (DNS rebinding)."""
    async def rebinding(host, port):
        return ["127.0.0.1"]

    f = SafeFetcher(make_settings(tmp_path), resolver=rebinding)
    f._preflight = lambda url: _noop()  # simulate a resolver that changed after pre-flight
    with pytest.raises(FetchError) as e:
        await f.fetch("https://rebind.example.com/")
    assert e.value.code == "blocked"
    await f.aclose()


async def _noop():
    return None


def test_redaction():
    text = redact("GET https://vision.googleapis.com/v1?key=AIzaSECRET token=abc Cookie: li_at=zzz Authorization: Bearer xyz")
    for secret in ("AIzaSECRET", "abc", "zzz", "xyz"):
        assert secret not in text


def test_session_store_never_exposes_cookie_values(tmp_path):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    s = make_settings(tmp_path, session_persistence_enabled=True, session_encryption_key=key)
    s.session_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(s)
    store.save("linkedin", [{"name": "li_at", "value": "SECRETVALUE", "domain": ".linkedin.com", "expires": -1},
                            {"name": "evil", "value": "x", "domain": ".attacker.com"}])
    status = store.status("linkedin")
    assert status["status"] == "logged_in" and status["cookie_count"] == 1
    assert "SECRETVALUE" not in str(store.all_status())
    path = s.session_dir / "linkedin.session"
    assert path.exists() and oct(path.stat().st_mode)[-3:] == "600"
    assert b"SECRETVALUE" not in path.read_bytes()  # encrypted at rest
    assert store.cookies_for_host("www.linkedin.com") == {"li_at": "SECRETVALUE"}
    assert store.cookies_for_host("evil-linkedin.com") is None
    assert SessionStore(s).status("linkedin")["status"] == "logged_in"  # reload from encrypted file


def test_sessions_are_memory_only_by_default(tmp_path):
    s = make_settings(tmp_path)
    s.session_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(s).save("xing", [{"name": "a", "value": "b", "domain": ".xing.com"}])
    assert not list(s.session_dir.iterdir())
