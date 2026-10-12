#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp==2.3.0", "reportlab==5.0.1"]
# ///
"""End-to-end smoke test against the compose stack.

    uv run scripts/smoke_test.py             # direct ports (llm-wiki :8080, wiki-inbox :8081)
    uv run scripts/smoke_test.py --gateway   # one multiplexed endpoint via agentgateway :3000

Ports default to the values in compose/.env (LLM_WIKI_PORT, WIKI_INBOX_PORT,
AGENTGATEWAY_PORT). Exits non-zero on the first failed check.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import Client

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "compose"

WIKI_TOOLS = {
    "wiki_spaces_list", "wiki_content_new", "wiki_content_read", "wiki_content_write",
    "wiki_content_commit", "wiki_ingest", "wiki_search", "wiki_list", "wiki_suggest",
}  # fmt: skip
INBOX_TOOLS = {
    "inbox_list", "inbox_read", "inbox_add", "inbox_add_url", "inbox_archive", "inbox_reject",
}  # fmt: skip

# llm-wiki 1.0.1 (rmcp) negotiates protocol 2026-07-28 with the mcp 2.x client
# but omits fields that version requires (cacheScope/ttlMs on tools/list).
# Use the classic initialize handshake, which is what current agents speak.
CLIENT_MODE = "legacy"


class SmokeFailure(Exception):
    pass


def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def skip(msg: str) -> None:
    print(f"  \033[33m-\033[0m SKIP {msg}")


def check(cond: bool, msg: str, detail: Any = None) -> None:
    if cond:
        ok(msg)
        return
    print(f"  \033[31m✗ {msg}\033[0m")
    if detail is not None:
        print(f"    {detail}")
    raise SmokeFailure(msg)


def step(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def env_value(key: str, default: str) -> str:
    env = COMPOSE / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip() or default
    return default


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def make_pdf(path: Path, text: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 750, text)
    c.showPage()
    c.save()


class Tools:
    """Routes a tool name to the client that serves it."""

    def __init__(self, wiki: Client, inbox: Client) -> None:
        self.wiki, self.inbox = wiki, inbox

    async def call(
        self, name: str, args: dict[str, Any] | None = None, *, expect_error: bool = False
    ):
        client = self.inbox if name.startswith("inbox_") else self.wiki
        result = await client.call_tool(name, args or {})
        text = "".join(getattr(c, "text", "") for c in result.content)
        if result.is_error != expect_error:
            check(False, f"{name}({args}) is_error={result.is_error}", text[:500])
        if result.structured_content is not None:
            return result.structured_content
        try:
            return json.loads(text)
        except ValueError:
            return text


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--gateway", action="store_true", help="go through agentgateway")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--wiki", default=env_value("LLM_WIKI_BOOTSTRAP_WIKI", "main"))
    parser.add_argument("--data-dir", type=Path, default=COMPOSE / "data" / "wikis")
    parser.add_argument(
        "--url-fixture",
        default="https://example.com/",
        help="public URL for inbox_add_url ('' to skip)",
    )
    args = parser.parse_args()

    token = uuid.uuid4().hex[:8]
    repo = args.data_dir / args.wiki
    inbox_dir = repo / "inbox"

    async with AsyncExitStack() as stack:
        step("1. tools/list")
        if args.gateway:
            url = f"http://{args.host}:{env_value('AGENTGATEWAY_PORT', '3000')}/mcp"
            client = await stack.enter_async_context(Client(url, mode=CLIENT_MODE))
            names = {t.name for t in (await client.list_tools()).tools}
            check(
                WIKI_TOOLS | INBOX_TOOLS <= names,
                f"gateway {url} exposes wiki_* and inbox_* tools",
                sorted(names),
            )
            tools = Tools(client, client)
        else:
            wiki_url = f"http://{args.host}:{env_value('LLM_WIKI_PORT', '8080')}/mcp"
            inbox_url = f"http://{args.host}:{env_value('WIKI_INBOX_PORT', '8081')}/mcp"
            wiki = await stack.enter_async_context(Client(wiki_url, mode=CLIENT_MODE))
            inbox = await stack.enter_async_context(Client(inbox_url, mode=CLIENT_MODE))
            wiki_names = {t.name for t in (await wiki.list_tools()).tools}
            inbox_names = {t.name for t in (await inbox.list_tools()).tools}
            check(
                WIKI_TOOLS <= wiki_names,
                f"llm-wiki {wiki_url}: {len(wiki_names)} tools",
                sorted(WIKI_TOOLS - wiki_names),
            )
            check(inbox_names == INBOX_TOOLS, f"wiki-inbox {inbox_url}: {sorted(inbox_names)}")
            tools = Tools(wiki, inbox)

        step("2. wiki_spaces_list")
        spaces = await tools.call("wiki_spaces_list")
        check(
            any(sp.get("name") == args.wiki for sp in spaces),
            f"'{args.wiki}' is registered",
            spaces,
        )
        info = await tools.call("wiki_info")
        check(info.get("default_wiki") == args.wiki, f"'{args.wiki}' is the default wiki", info)

        step("3. queue sources")
        note = await tools.call(
            "inbox_add",
            {
                "filename": f"smoke-note-{token}.md",
                "content": f"# Smoke note {token}\n\nZebrafish{token} regenerate their fins.\n",
            },
        )
        check(note["file"] == f"smoke-note-{token}.md", f"inbox_add → {note['path']}")
        reject_me = await tools.call("inbox_add", {"filename": f"junk-{token}", "content": "lorem"})
        ok(f"inbox_add → {reject_me['path']} (to be rejected)")

        url_file = None
        if args.url_fixture:
            client = tools.inbox
            res = await client.call_tool(
                "inbox_add_url", {"url": args.url_fixture, "filename": f"web-{token}"}
            )
            if res.is_error:
                skip(f"inbox_add_url {args.url_fixture} (offline?): {res.content[0].text[:200]}")
            else:
                url_file = res.structured_content["file"]
                ok(f"inbox_add_url {args.url_fixture} → inbox/{url_file}")
        else:
            skip("inbox_add_url (no --url-fixture)")

        pdf_name = f"syncthing-drop-{token}.pdf"
        make_pdf(inbox_dir / pdf_name, f"Axolotl{token} limb regeneration study")
        ok(f"dropped inbox/{pdf_name} on disk (simulated Syncthing)")

        step("4. inbox_list / inbox_read")
        listing = await tools.call("inbox_list", {})
        names = [f["name"] for f in listing["files"]]
        expected = {note["file"], reject_me["file"], pdf_name} | ({url_file} if url_file else set())
        check(expected <= set(names), f"inbox_list sees {len(expected)} new files", names)
        mtimes = [f["mtime"] for f in listing["files"]]
        check(mtimes == sorted(mtimes), "sorted oldest first")
        pdf = await tools.call("inbox_read", {"file": pdf_name})
        check(
            f"Axolotl{token}" in pdf["content"],
            f"inbox_read converts PDF → Markdown ({pdf['total_chars']} chars)",
            pdf,
        )

        step("5. create a page")
        slug = f"concepts/smoke-{token}"
        created = await tools.call(
            "wiki_content_new", {"uri": slug, "type": "concept", "name": f"Smoke {token}"}
        )
        check(created.get("slug") == slug, f"wiki_content_new → {created}")
        scaffold = await tools.call("wiki_content_read", {"uri": slug})
        check(
            isinstance(scaffold, str) and scaffold.startswith("---"),
            "wiki_content_read returns scaffold",
        )
        body = (
            f'---\ntitle: "Smoke {token}"\ntype: concept\nstatus: active\n'
            f'summary: "Smoke-test page about Zebrafish{token} fin regeneration."\n'
            f"tags: [smoke-test]\nconfidence: 0.5\n---\n\n"
            f"# Smoke {token}\n\nZebrafish{token} regenerate their fins (from inbox/{note['file']}).\n"
        )
        await tools.call("wiki_content_write", {"uri": slug, "content": body})
        ok("wiki_content_write")
        report = await tools.call("wiki_ingest", {"path": slug + ".md"})
        check(
            isinstance(report, dict) and report.get("pages_validated", 0) >= 1,
            f"wiki_ingest validated + committed {report.get('commit', '')[:8]}",
            report,
        )

        step("6. wiki_search")
        found = None
        for _ in range(10):
            hits = await tools.call("wiki_search", {"query": f"Zebrafish{token}"})
            results = hits.get("results", []) if isinstance(hits, dict) else []
            found = next((r for r in results if r.get("slug") == slug), None)
            if found:
                break
            await asyncio.sleep(0.5)
        check(found is not None, f"wiki_search finds {slug}", hits)

        step("7. archive / reject / commit")
        archived = await tools.call("inbox_archive", {"file": note["file"]})
        check(archived["to"] == f"raw/{note['file']}", f"inbox_archive → {archived['to']}")
        rejected = await tools.call(
            "inbox_reject", {"file": reject_me["file"], "reason": "smoke test: no content"}
        )
        check(rejected["to"].startswith("inbox/_failed/"), f"inbox_reject → {rejected['to']}")
        commit = await tools.call("wiki_content_commit", {"message": f"smoke: archive {token}"})
        ok(f"wiki_content_commit → {str(commit)[:60]}")

        step("8. git log")
        log = await asyncio.to_thread(git, repo, "log", "--stat", "-n", "3", "--format=%h %an: %s")
        print("    " + log.strip().replace("\n", "\n    "))
        tracked = (await asyncio.to_thread(git, repo, "ls-files")).splitlines()
        check(f"wiki/{slug}.md" in tracked, "page is committed")
        check(f"raw/{note['file']}" in tracked, "raw/ move is committed")
        if f"inbox/{note['file']}" in tracked:
            print(
                "    note: inbox/ deletion not staged by wiki_content_commit (see docs/decisions.md)"
            )
        else:
            ok("inbox/ deletion is committed")

        step("9. path traversal")
        await tools.call("inbox_read", {"file": "../wiki.toml"}, expect_error=True)
        ok("inbox_read ../wiki.toml rejected")
        await tools.call("inbox_archive", {"file": "../wiki.toml"}, expect_error=True)
        ok("inbox_archive ../wiki.toml rejected")
        await tools.call("inbox_list", {"wiki": "/etc"}, expect_error=True)
        ok("raw filesystem path as wiki name rejected")

        # Tidy up the leftovers so the inbox stays clean between runs.
        for leftover in (pdf_name, url_file):
            if leftover:
                await tools.call("inbox_archive", {"file": leftover})
        await tools.call("wiki_content_commit", {"message": f"smoke: cleanup {token}"})

    print("\n\033[32mSmoke test passed.\033[0m")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except* SmokeFailure:
        print("\n\033[31mSmoke test FAILED.\033[0m")
        sys.exit(1)
