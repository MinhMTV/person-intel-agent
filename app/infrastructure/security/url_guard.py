"""SSRF protection: decide whether an outbound URL / IP may be contacted."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Awaitable[list[str]]]

_BLOCKED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local incl. 169.254.169.254 metadata
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",  # IPv4-mapped — checked via the mapped address below
        "64:ff9b::/96",
        "100::/64",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::254/128",  # AWS IMDS over IPv6
    )
]
_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


class BlockedURLError(ValueError):
    """Raised when a URL targets a disallowed scheme/host/network."""


def is_ip_allowed(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return is_ip_allowed(str(addr.ipv4_mapped))
    if any(addr in net for net in _BLOCKED_NETWORKS):
        return False
    return addr.is_global and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast)


def parse_public_url(url: str) -> tuple[str, str, int]:
    """Syntactic checks. Returns (scheme, host, port) or raises BlockedURLError."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise BlockedURLError("Malformed URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise BlockedURLError(f"Scheme '{scheme or '?'}' is not allowed")
    if parts.username or parts.password:
        raise BlockedURLError("Credentials in URLs are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise BlockedURLError("URL has no host")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise BlockedURLError("Internal hostnames are not allowed")
    port = port or (443 if scheme == "https" else 80)
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal and not is_ip_allowed(host):
        raise BlockedURLError("Target address is not a public IP")
    if not literal and host.replace(".", "").isdigit():
        raise BlockedURLError("Numeric host encodings are not allowed")
    return scheme, host, port


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def resolve_public(host: str, port: int, resolver: Resolver = system_resolver) -> list[str]:
    """Resolve host and require that EVERY returned address is public."""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = await resolver(host, port)
        except OSError as exc:
            raise BlockedURLError(f"Could not resolve host '{host}'") from exc
    if not addresses:
        raise BlockedURLError(f"Host '{host}' has no addresses")
    for addr in addresses:
        if not is_ip_allowed(addr):
            raise BlockedURLError("Host resolves to a non-public address")
    return addresses
