"""Outbound URL guard (SSRF protection).

Every URL a user can type (website, web novel, novel images) goes through
``assert_public_url`` before Trarium connects to it, and ``safe_get`` follows
redirects by hand so each hop is checked too. Loopback, private, link-local
(cloud metadata 169.254.169.254), carrier-grade NAT, multicast and reserved
addresses are refused.

For local development against a server on your own machine, set
``TRARIUM_ALLOW_PRIVATE_URLS=1``. Never set it on a public deploy.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_REDIRECTS = 5
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
                      "metadata", "metadata.google.internal", "instance-data"}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedURLError(ValueError):
    """Raised when a URL points at an address Trarium must not reach."""


def private_urls_allowed() -> bool:
    return os.environ.get("TRARIUM_ALLOW_PRIVATE_URLS", "").strip().lower() in {"1", "true", "yes"}


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified
                or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT))


def _resolve(host: str) -> list[ipaddress._BaseAddress]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedURLError(f"نام دامنه پیدا نشد: {host}") from exc
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%", 1)[0]))
        except ValueError:
            continue
    if not out:
        raise BlockedURLError(f"نام دامنه پیدا نشد: {host}")
    return out


def assert_public_url(url: str) -> str:
    """Return ``url`` if it is an http(s) URL on a public address, else raise."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        raise BlockedURLError("فقط نشانی‌های http و https مجاز هستند.")
    if parsed.username or parsed.password:
        raise BlockedURLError("نشانی همراه با نام کاربری یا رمز مجاز نیست.")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise BlockedURLError("نشانی صفحه معتبر نیست.")
    if private_urls_allowed():
        return url
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".internal"):
        raise BlockedURLError("دسترسی به نشانی‌های داخلی یا محلی مجاز نیست.")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        addresses = _resolve(host)
    for ip in addresses:
        if _ip_is_blocked(ip):
            raise BlockedURLError("دسترسی به نشانی‌های داخلی یا محلی مجاز نیست.")
    return url


def safe_get(url: str, *, headers: dict | None = None, timeout: float = 25.0,
             max_redirects: int = MAX_REDIRECTS, same_host: bool = False) -> requests.Response:
    """GET that checks the target and every redirect hop before connecting.

    ``same_host=True`` also refuses a redirect to another host.
    """
    current = assert_public_url(url)
    first_host = (urlparse(current).hostname or "").lower()
    for _ in range(max(0, int(max_redirects)) + 1):
        response = requests.get(current, headers=headers or {}, timeout=timeout, allow_redirects=False)
        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                return response
            nxt = urljoin(current, location)
            if same_host and (urlparse(nxt).hostname or "").lower() != first_host:
                raise BlockedURLError("صفحه به دامنهٔ دیگری هدایت شد؛ دنبال نمی‌شود.")
            current = assert_public_url(nxt)
            continue
        return response
    raise BlockedURLError("تعداد هدایت‌ها (redirect) بیش از حد مجاز است.")
