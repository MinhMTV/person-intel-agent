from app.utils.canonical import (
    canonical_image_url,
    canonical_url,
    domain_of,
    name_tokens,
    normalize_email,
    normalize_name,
    normalize_username,
    registrable_domain,
    stable_hash,
)
from app.utils.platforms import platform_for, source_quality, username_from_url


def test_canonical_url_strips_noise():
    assert canonical_url("HTTP://WWW.Example.com:80/Path/?utm_source=x&b=2&a=1#frag") == "https://example.com/Path?a=1&b=2"
    assert canonical_url("https://example.com/a/") == canonical_url("https://example.com/a")
    assert canonical_url("https://twitter.com/jane") == canonical_url("https://x.com/jane")
    assert canonical_url("https://de.linkedin.com/in/Jane-Doe-123/details/") == "https://de.linkedin.com/in/jane-doe-123"
    assert canonical_url("javascript:alert(1)") == "javascript:alert(1)"  # never turned into http


def test_canonical_image_url_drops_size_params():
    assert canonical_image_url("https://avatars.example.com/u/1?s=400&v=4") == canonical_image_url("https://avatars.example.com/u/1?s=80")


def test_domains():
    assert domain_of("https://www.Example.co.uk/x") == "example.co.uk"
    assert registrable_domain("https://blog.example.co.uk/x") == "example.co.uk"
    assert registrable_domain("https://a.b.github.io") == "github.io"


def test_identifiers():
    assert normalize_email("Jane.Doe+news@GoogleMail.com") == "janedoe@gmail.com"
    assert normalize_email("not-an-email") == ""
    assert normalize_username("@JaneDoe93 ") == "janedoe93"
    assert normalize_name("  Jürgen  Müller-Lüdenscheidt ") == "jurgen muller ludenscheidt"
    assert name_tokens("Dr. J. Doe") == ["dr", "doe"]


def test_stable_hash_is_order_independent_for_dicts():
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    assert stable_hash({"a": 1}) != stable_hash({"a": 2})


def test_platform_helpers():
    assert platform_for("https://github.com/janedoe93") == "github"
    assert username_from_url("https://github.com/janedoe93") == "janedoe93"
    assert username_from_url("https://github.com/features") is None
    assert username_from_url("https://www.linkedin.com/in/jane-doe/") == "jane-doe"
    assert source_quality("https://github.com/janedoe93") > source_quality("https://random.example.com/x")
