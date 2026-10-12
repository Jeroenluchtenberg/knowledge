from pathlib import Path

import httpx
import pytest
from mcp import Client

from wiki_inbox.config import Settings
from wiki_inbox.server import READ_TOOLS, WRITE_TOOLS, build_server, create_app


async def tool_names(settings: Settings) -> set[str]:
    async with Client(build_server(settings)) as client:
        return {t.name for t in (await client.list_tools()).tools}


async def test_all_tools_registered(settings: Settings) -> None:
    assert await tool_names(settings) == set(READ_TOOLS) | set(WRITE_TOOLS)


async def test_read_only_hides_mutating_tools(settings: Settings) -> None:
    ro = settings.model_copy(update={"read_only": True})
    assert await tool_names(ro) == set(READ_TOOLS)


async def test_call_over_mcp(settings: Settings, main_inbox: Path) -> None:
    async with Client(build_server(settings)) as client:
        added = await client.call_tool("inbox_add", {"filename": "n", "content": "# hi"})
        assert not added.is_error and added.structured_content["file"] == "n.md"
        listed = await client.call_tool("inbox_list", {})
        assert [f["name"] for f in listed.structured_content["files"]] == ["n.md"]
        bad = await client.call_tool("inbox_read", {"file": "../wiki.toml"})
        assert bad.is_error
        bad_wiki = await client.call_tool("inbox_list", {"wiki": "/etc"})
        assert bad_wiki.is_error


@pytest.fixture
def http_client(settings: Settings):
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://wiki-inbox:8081")


async def test_healthz(http_client: httpx.AsyncClient) -> None:
    async with http_client as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


async def test_host_allow_list(settings: Settings) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://wiki-inbox:8081") as c:
            ok = await c.post("/mcp", json=INIT, headers=HEADERS)
            assert ok.status_code == 200, ok.text
            evil = await c.post("/mcp", json=INIT, headers={**HEADERS, "host": "evil.example"})
            assert evil.status_code == 421
