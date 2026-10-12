import ipaddress
import json
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.fixtures import HTML
from wiki_inbox.config import Settings
from wiki_inbox.fetch import FetchError, check_url, fetch_url, is_blocked_ip, pick_address
from wiki_inbox.server import Inbox

PUBLIC_IP = "93.184.215.14"


def resolver_for(mapping: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        return mapping[host]

    return resolve


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:0a00:0001::1",
    ],
)  # fmt: skip
def test_blocked_ips(ip: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", [PUBLIC_IP, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    assert not is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/x", "file:///etc/passwd", "gopher://x", "http://", "http://u:p@x.com/"],
)
def test_check_url_rejects(url: str) -> None:
    with pytest.raises(FetchError):
        check_url(url)


@pytest.mark.parametrize(
    "addrs", [["127.0.0.1"], ["10.0.0.5"], ["169.254.169.254"], [PUBLIC_IP, "10.0.0.1"]]
)
async def test_dns_to_private_blocked(addrs: list[str]) -> None:
    with pytest.raises(FetchError, match="non-public"):
        await pick_address(
            "evil.test", 80, allow_private=False, resolver=resolver_for({"evil.test": addrs})
        )


async def test_ip_literal_blocked_without_dns() -> None:
    async def no_dns(host: str, port: int) -> list[str]:
        raise AssertionError("must not resolve literals")

    with pytest.raises(FetchError):
        await pick_address("127.0.0.1", 80, allow_private=False, resolver=no_dns)


async def test_allow_private() -> None:
    addr = await pick_address(
        "intranet", 80, allow_private=True, resolver=resolver_for({"intranet": ["10.0.0.5"]})
    )
    assert addr == "10.0.0.5"


async def test_fetch_pins_ip_and_sets_host() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"hello")

    result = await fetch_url(
        "https://example.test/a?b=1",
        max_bytes=100,
        timeout=5,
        allow_private=False,
        resolver=resolver_for({"example.test": [PUBLIC_IP]}),
        transport=httpx.MockTransport(handler),
    )
    assert result.body == b"hello" and result.content_type == "text/plain"
    req = seen[0]
    assert req.url.host == PUBLIC_IP and req.url.query == b"b=1"
    assert req.headers["host"] == "example.test"
    assert req.extensions["sni_hostname"] == "example.test"


async def test_redirect_to_private_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://metadata.test/latest"})

    with pytest.raises(FetchError, match="non-public"):
        await fetch_url(
            "http://example.test/",
            max_bytes=100,
            timeout=5,
            allow_private=False,
            resolver=resolver_for(
                {"example.test": [PUBLIC_IP], "metadata.test": ["169.254.169.254"]}
            ),
            transport=httpx.MockTransport(handler),
        )


async def test_redirect_loop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(FetchError, match="too many redirects"):
        await fetch_url(
            "http://example.test/",
            max_bytes=100,
            timeout=5,
            allow_private=False,
            resolver=resolver_for({"example.test": [PUBLIC_IP]}),
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.parametrize("declare", [True, False])
async def test_size_cap(declare: bool) -> None:
    body = b"a" * 500

    def handler(request: httpx.Request) -> httpx.Response:
        if declare:
            return httpx.Response(200, content=body)

        async def chunks():
            yield body[:250]
            yield body[250:]

        return httpx.Response(200, content=chunks())

    with pytest.raises(FetchError, match="limit"):
        await fetch_url(
            "http://example.test/",
            max_bytes=100,
            timeout=5,
            allow_private=False,
            resolver=resolver_for({"example.test": [PUBLIC_IP]}),
            transport=httpx.MockTransport(handler),
        )


async def test_http_error_status() -> None:
    with pytest.raises(FetchError, match="404"):
        await fetch_url(
            "http://example.test/",
            max_bytes=100,
            timeout=5,
            allow_private=False,
            resolver=resolver_for({"example.test": [PUBLIC_IP]}),
            transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        )


# ── inbox_add_url end-to-end with a mocked network ──────────────────────────


def mocked_inbox(settings: Settings, monkeypatch: pytest.MonkeyPatch, handler) -> Inbox:
    import wiki_inbox.server as server

    real = server.fetch_url

    async def fake_fetch(url: str, **kw):
        return await real(url, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(server, "fetch_url", fake_fetch)
    return Inbox(settings, resolver_for({"example.test": [PUBLIC_IP]}))


async def test_add_url_html_converted(settings: Settings, monkeypatch, main_inbox: Path) -> None:
    inbox = mocked_inbox(
        settings,
        monkeypatch,
        lambda r: httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML
        ),
    )
    r = await inbox.add_url("http://example.test/posts/gd.html")
    assert r["file"] == "example.test-gd.md" and r["converted"] is True
    text = (main_inbox / r["file"]).read_text()
    assert text.startswith("> Source: <http://example.test/posts/gd.html>")
    assert "# Gradient descent" in text and not text.startswith("---")
    assert r["source_sidecar"] is None


async def test_add_url_binary_sidecar(settings: Settings, monkeypatch, main_inbox: Path) -> None:
    inbox = mocked_inbox(
        settings,
        monkeypatch,
        lambda r: httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"
        ),
    )
    r = await inbox.add_url("http://example.test/download?id=3", filename="My Paper")
    assert r["file"] == "My Paper.pdf"
    side = json.loads((main_inbox / "My Paper.pdf.source.json").read_text())
    assert side["source_url"] == "http://example.test/download?id=3"
    assert side["content_type"] == "application/pdf" and "fetched_at" in side
    assert r["source_sidecar"] == "inbox/My Paper.pdf.source.json"


async def test_add_url_blocked(settings: Settings) -> None:
    inbox = Inbox(settings, resolver_for({"internal.test": ["10.0.0.1"]}))
    with pytest.raises(ToolError, match="non-public"):
        await inbox.add_url("http://internal.test/")
