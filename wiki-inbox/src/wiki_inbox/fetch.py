"""Server-side URL fetching with an SSRF guard.

Every hop (including redirects) is resolved once, checked against the
private-address block list, and then connected to *by IP*, with the original
hostname sent as Host header and TLS SNI. That pins the connection to the
checked address and defeats DNS-rebinding between check and connect.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_REDIRECTS = 5
USER_AGENT = "wiki-inbox/0.1 (+https://github.com/Jeroenluchtenberg/knowledge)"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """The URL was rejected or could not be fetched."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {host}: {e}") from e
    return [str(info[4][0]) for info in infos]


def is_blocked_ip(ip: IPAddress) -> bool:
    """True for anything that is not a public unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return is_blocked_ip(ip.sixtofour)
        if ip.teredo is not None:
            return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_url(url: str) -> tuple[str, str, int]:
    """Validate scheme/host; return ``(scheme, host, port)``."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise FetchError(f"only http/https URLs are allowed, got {scheme or 'none'!r}")
    if not parts.hostname:
        raise FetchError(f"URL has no host: {url!r}")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials are not allowed")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as e:
        raise FetchError(f"invalid port in URL: {url!r}") from e
    return scheme, parts.hostname, port


async def pick_address(
    host: str, port: int, *, allow_private: bool, resolver: Resolver = system_resolver
) -> str:
    """Resolve *host* and return an address to connect to, enforcing the guard.

    All resolved addresses must be allowed — a mix of public and private is rejected.
    """
    try:
        literal: IPAddress | None = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addrs = [str(literal)] if literal is not None else await resolver(host, port)
    if not addrs:
        raise FetchError(f"{host} did not resolve to any address")
    if not allow_private:
        for addr in addrs:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
            if is_blocked_ip(ip):
                raise FetchError(f"refusing to fetch {host}: resolves to non-public address {ip}")
    return addrs[0]


def _pinned_url(url: str, address: str) -> str:
    parts = urlsplit(url)
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    host = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


async def fetch_url(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
    allow_private: bool,
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Fetch *url* following up to ``MAX_REDIRECTS`` redirects, streaming with a size cap."""
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            scheme, host, port = check_url(current)
            address = await pick_address(host, port, allow_private=allow_private, resolver=resolver)
            host_header = urlsplit(current).netloc  # credentials are rejected by check_url
            extensions = {"sni_hostname": host} if scheme == "https" else {}
            request = client.build_request(
                "GET",
                _pinned_url(current, address),
                headers={"Host": host_header},
                extensions=extensions,
            )
            try:
                response = await client.send(request, stream=True)
            except httpx.HTTPError as e:
                raise FetchError(f"fetching {current} failed: {e}") from e
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError(f"redirect from {current} without Location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise FetchError(f"{current} returned HTTP {response.status_code}")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError(
                        f"{current} is {int(declared)} bytes; limit is {max_bytes} bytes"
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(f"{current} exceeds the {max_bytes} byte limit")
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "")
                return FetchResult(
                    url=url,
                    final_url=current,
                    status=response.status_code,
                    content_type=content_type.split(";", 1)[0].strip().lower(),
                    body=b"".join(chunks),
                )
            except httpx.HTTPError as e:
                raise FetchError(f"fetching {current} failed: {e}") from e
            finally:
                await response.aclose()
    raise FetchError(f"too many redirects (>{MAX_REDIRECTS}) starting at {url}")
