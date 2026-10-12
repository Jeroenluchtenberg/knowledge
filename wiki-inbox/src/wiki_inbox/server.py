"""wiki-inbox MCP server: list, read, add, fetch, archive and reject inbox files.

wiki-inbox never runs git. File moves are committed by llm-wiki
(``wiki_content_commit``), so only one process ever touches ``.git``.
"""

import json
import mimetypes
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import unquote, urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from wiki_inbox import __version__
from wiki_inbox.config import Settings, Wiki, WikiError, get_settings, resolve_wiki
from wiki_inbox.convert import (
    ConversionError,
    detect_type,
    html_to_markdown,
    is_convertible,
    to_markdown,
)
from wiki_inbox.fetch import FetchError, Resolver, fetch_url, system_resolver
from wiki_inbox.paths import (
    FAILED_DIR,
    REASON_SUFFIX,
    SOURCE_SUFFIX,
    PathError,
    existing_file,
    is_hidden,
    is_sync_conflict,
    is_syncthing_temp,
    resolve_inside,
    sanitize_filename,
    source_sidecar,
    unique_path,
)

READ_TOOLS = ("inbox_list", "inbox_read")
WRITE_TOOLS = ("inbox_add", "inbox_add_url", "inbox_archive", "inbox_reject")

INSTRUCTIONS = """\
Ingestion queue for llm-wiki. Files land in a wiki's inbox/ (via inbox_add,
inbox_add_url, or Syncthing). Agents read them with inbox_read (binary formats
are converted to Markdown), write wiki pages with the llm-wiki wiki_* tools,
then call inbox_archive (moves the file to raw/) or inbox_reject (moves it to
inbox/_failed/ with a reason). Moves are not committed here: call
wiki_content_commit afterwards. All paths are relative to the wiki's inbox/.
"""

WikiArg = Annotated[
    str | None, Field(description="Wiki name (as registered in llm-wiki); default wiki if omitted")
]
FileArg = Annotated[str, Field(description="File path relative to the wiki's inbox/")]

_CONTENT_TYPE_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    "application/pdf": ".pdf",
    "application/json": ".json",
    "text/csv": ".csv",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/epub+zip": ".epub",
}
_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
_FETCH = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_MARKDOWN_TYPES = {"text/markdown", "text/x-markdown", "text/plain"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file + rename so Syncthing never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".wiki-inbox-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # Do not overwrite something that appeared meanwhile.
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


def _write_new(directory: Path, filename: str, data: bytes) -> Path:
    """Write *data* under a collision-free name; never overwrites."""
    for _ in range(100):
        target = unique_path(directory, filename)
        try:
            _atomic_write(target, data)
            return target
        except FileExistsError:
            continue
    raise ToolError(f"could not find a free name for {filename}")


def _strip_ext(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return stem if dot and stem and ext.isalnum() and len(ext) <= 10 else name


def _remove_empty_parents(path: Path, stop: Path) -> None:
    parent = path.parent
    while parent != stop and parent.is_relative_to(stop):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


class Inbox:
    """Tool implementations, independent of the MCP transport (unit-testable)."""

    def __init__(self, settings: Settings, resolver: Resolver = system_resolver) -> None:
        self.settings = settings
        self.resolver = resolver

    # ── helpers ──────────────────────────────────────────────────────────────

    def wiki(self, name: str | None) -> Wiki:
        try:
            wiki = resolve_wiki(name, self.settings)
        except WikiError as e:
            raise ToolError(str(e)) from e
        if not wiki.root.is_dir():
            raise ToolError(f"wiki {wiki.name!r} repository is missing on disk")
        return wiki

    def _inbox_file(self, wiki: Wiki, file: str) -> tuple[Path, PurePosixPath]:
        try:
            return existing_file(wiki.inbox, file)
        except PathError as e:
            raise ToolError(str(e)) from e

    def _repo_rel(self, wiki: Wiki, path: Path) -> str:
        return path.relative_to(wiki.root).as_posix()

    # ── read tools ───────────────────────────────────────────────────────────

    def list_files(self, wiki: str | None = None) -> dict[str, Any]:
        w = self.wiki(wiki)
        files: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        if w.inbox.is_dir():
            inbox = w.inbox.resolve()
            for dirpath, dirnames, filenames in os.walk(inbox):
                current = Path(dirpath)
                rel_dir = current.relative_to(inbox)
                # Prune _failed/ and hidden directories (.stfolder, .stversions, …).
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if not d.startswith(".") and not (rel_dir == Path(".") and d == FAILED_DIR)
                )
                for name in filenames:
                    path = current / name
                    rel = PurePosixPath((rel_dir / name).as_posix())
                    if is_hidden(rel) or is_syncthing_temp(name) or not path.is_file():
                        continue
                    if (
                        name.endswith(SOURCE_SUFFIX)
                        and (current / name[: -len(SOURCE_SUFFIX)]).exists()
                    ):
                        continue  # provenance sidecar, reported via has_source
                    try:
                        resolve_inside(inbox, rel)
                    except PathError:
                        continue  # symlink pointing outside the inbox
                    st = path.stat()
                    entry = {
                        "name": rel.as_posix(),
                        "size": st.st_size,
                        "mtime": _iso(st.st_mtime),
                        "type": detect_type(path),
                        "convertible": st.st_size <= self.settings.max_bytes
                        and is_convertible(path),
                        "has_source": source_sidecar(path).exists(),
                        "_mtime": st.st_mtime,
                    }
                    (conflicts if is_sync_conflict(name) else files).append(entry)
        for bucket in (files, conflicts):
            bucket.sort(key=lambda e: (e["_mtime"], e["name"]))
            for e in bucket:
                del e["_mtime"]
        return {"wiki": w.name, "files": files, "conflicts": conflicts}

    def read(
        self, file: str, wiki: str | None = None, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        w = self.wiki(wiki)
        path, rel = self._inbox_file(w, file)
        if offset < 0:
            raise ToolError("offset must be >= 0")
        limit = self.settings.read_chunk if limit is None else limit
        if limit <= 0:
            raise ToolError("limit must be > 0")
        size = path.stat().st_size
        if size > self.settings.max_bytes:
            raise ToolError(
                f"{rel} is {size} bytes; limit is {self.settings.max_bytes}. "
                "Use inbox_reject to move it aside."
            )
        try:
            text = to_markdown(path, self.settings.cache_dir)
        except ConversionError as e:
            raise ToolError(
                f"{e}. If the file is unusable, call inbox_reject(file={rel.as_posix()!r}, "
                "reason=...)."
            ) from e
        total = len(text)
        start = min(offset, total)
        end = min(start + limit, total)
        return {
            "file": rel.as_posix(),
            "content": text[start:end],
            "offset": start,
            "next_offset": end if end < total else None,
            "total_chars": total,
            "truncated": end < total,
        }

    # ── write tools ──────────────────────────────────────────────────────────

    def add(self, filename: str, content: str, wiki: str | None = None) -> dict[str, Any]:
        w = self.wiki(wiki)
        data = content.encode("utf-8")
        if len(data) > self.settings.max_bytes:
            raise ToolError(f"content is {len(data)} bytes; limit is {self.settings.max_bytes}")
        name = sanitize_filename(filename, ".md")
        target = _write_new(w.inbox, name, data)
        return {
            "wiki": w.name,
            "file": target.name,
            "path": self._repo_rel(w, target),
            "bytes": len(data),
        }

    async def add_url(
        self, url: str, wiki: str | None = None, filename: str | None = None
    ) -> dict[str, Any]:
        w = self.wiki(wiki)
        try:
            result = await fetch_url(
                url,
                max_bytes=self.settings.max_bytes,
                timeout=self.settings.fetch_timeout,
                allow_private=self.settings.fetch_allow_private,
                resolver=self.resolver,
            )
        except FetchError as e:
            raise ToolError(str(e)) from e

        url_path = PurePosixPath(unquote(urlsplit(result.final_url).path))
        ext = (
            _CONTENT_TYPE_EXT.get(result.content_type)
            or mimetypes.guess_extension(result.content_type or "")
            or url_path.suffix.lower()
            or ".bin"
        )
        if filename:
            base = _strip_ext(filename)
        else:
            host = urlsplit(result.final_url).hostname or "download"
            base = f"{host}-{url_path.stem}" if url_path.stem else host
        fetched_at = result.fetched_at.isoformat(timespec="seconds")
        provenance = {
            "source_url": result.url,
            "final_url": result.final_url,
            "content_type": result.content_type,
            "fetched_at": fetched_at,
            "bytes": len(result.body),
        }
        header = f"> Source: <{result.final_url}>  \n> Fetched: {fetched_at}\n\n"

        markdown: str | None = None
        if result.content_type in _HTML_TYPES or ext in (".html", ".htm"):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_html = Path(tmp) / "page.html"
                tmp_html.write_bytes(result.body)
                try:
                    markdown = html_to_markdown(tmp_html)
                except ConversionError:
                    markdown = None  # keep the raw HTML + sidecar instead
        elif result.content_type in _MARKDOWN_TYPES or ext in (".md", ".txt"):
            markdown = result.body.decode("utf-8", errors="replace")

        if markdown is not None:
            name = sanitize_filename(f"{base}.md")
            target = _write_new(w.inbox, name, (header + markdown).encode("utf-8"))
            sidecar = None
        else:
            name = sanitize_filename(f"{base}{ext}", ext)
            target = _write_new(w.inbox, name, result.body)
            sidecar_path = source_sidecar(target)
            sidecar_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
            sidecar = self._repo_rel(w, sidecar_path)

        return {
            "wiki": w.name,
            "file": target.name,
            "path": self._repo_rel(w, target),
            "content_type": result.content_type,
            "bytes": target.stat().st_size,
            "converted": markdown is not None and result.content_type in _HTML_TYPES,
            "source_sidecar": sidecar,
        }

    def _move(self, src: Path, dest_dir: Path, dest_name: str) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(dest_dir, dest_name)
        shutil.move(src, target)
        side = source_sidecar(src)
        if side.exists():
            shutil.move(side, unique_path(dest_dir, target.name + SOURCE_SUFFIX))
        return target

    def archive(self, file: str, wiki: str | None = None) -> dict[str, Any]:
        w = self.wiki(wiki)
        src, rel = self._inbox_file(w, file)
        if rel.parts[0] == FAILED_DIR:
            raise ToolError(f"{rel} is in {FAILED_DIR}/; move it back to the inbox first")
        try:
            dest = resolve_inside(w.raw, rel)
        except PathError as e:
            raise ToolError(str(e)) from e
        target = self._move(src, dest.parent, dest.name)
        _remove_empty_parents(src, w.inbox.resolve())
        return {
            "wiki": w.name,
            "from": f"inbox/{rel.as_posix()}",
            "to": self._repo_rel(w, target),
            "committed": False,
            "note": "Not committed; call wiki_content_commit after the batch.",
        }

    def reject(self, file: str, reason: str, wiki: str | None = None) -> dict[str, Any]:
        w = self.wiki(wiki)
        if not reason.strip():
            raise ToolError("reason must not be empty")
        src, rel = self._inbox_file(w, file)
        if rel.parts[0] == FAILED_DIR:
            raise ToolError(f"{rel} is already in {FAILED_DIR}/")
        failed = w.inbox / FAILED_DIR
        try:
            dest = resolve_inside(failed, rel)
        except PathError as e:
            raise ToolError(str(e)) from e
        target = self._move(src, dest.parent, dest.name)
        _remove_empty_parents(src, w.inbox.resolve())
        reason_path = target.with_name(target.name + REASON_SUFFIX)
        reason_path.write_text(
            f"# Rejected: {rel.as_posix()}\n\n- Time: {_now()}\n- File: {target.name}\n\n"
            f"## Reason\n\n{reason.strip()}\n",
            encoding="utf-8",
        )
        return {
            "wiki": w.name,
            "from": f"inbox/{rel.as_posix()}",
            "to": self._repo_rel(w, target),
            "reason_file": self._repo_rel(w, reason_path),
        }


def build_server(
    settings: Settings | None = None, resolver: Resolver = system_resolver
) -> MCPServer:
    """Create the MCP server; mutating tools are skipped in read-only mode."""
    settings = settings or get_settings()
    inbox = Inbox(settings, resolver)
    mcp: MCPServer = MCPServer("wiki-inbox", version=__version__, instructions=INSTRUCTIONS)

    @mcp.tool(annotations=_READ)
    def inbox_list(wiki: WikiArg = None) -> dict[str, Any]:
        """List files waiting in the wiki's inbox/, oldest first.

        Excludes inbox/_failed/, dotfiles and Syncthing temp files; Syncthing
        conflict copies are listed separately under `conflicts`.
        """
        return inbox.list_files(wiki)

    @mcp.tool(annotations=_READ)
    def inbox_read(
        file: FileArg,
        wiki: WikiArg = None,
        offset: Annotated[int, Field(description="Character offset to start at", ge=0)] = 0,
        limit: Annotated[
            int | None,
            Field(description=f"Max characters to return (default {settings.read_chunk})", gt=0),
        ] = None,
    ) -> dict[str, Any]:
        """Read an inbox file as Markdown (PDF/DOCX/PPTX/XLSX/HTML are converted).

        Page through long documents: call again with offset=next_offset until
        next_offset is null.
        """
        return inbox.read(file, wiki, offset, limit)

    if settings.read_only:
        return mcp

    @mcp.tool(annotations=_WRITE)
    def inbox_add(
        filename: Annotated[
            str, Field(description="File name; sanitized, .md added if no extension")
        ],
        content: Annotated[str, Field(description="UTF-8 text/Markdown content")],
        wiki: WikiArg = None,
    ) -> dict[str, Any]:
        """Queue a text/Markdown note in the inbox. Never overwrites: a -1, -2… suffix
        is added on collision. Returns the final file name."""
        return inbox.add(filename, content, wiki)

    @mcp.tool(annotations=_FETCH)
    async def inbox_add_url(
        url: Annotated[str, Field(description="http(s) URL to fetch server-side")],
        wiki: WikiArg = None,
        filename: Annotated[str | None, Field(description="Optional file name")] = None,
    ) -> dict[str, Any]:
        """Fetch a URL into the inbox. HTML is converted to Markdown with a source
        header; other types are stored as-is with a <name>.source.json sidecar."""
        return await inbox.add_url(url, wiki, filename)

    @mcp.tool(annotations=_WRITE)
    def inbox_archive(file: FileArg, wiki: WikiArg = None) -> dict[str, Any]:
        """Move a processed inbox file to raw/ (keeps subfolders, suffix on collision).
        Does not commit — call wiki_content_commit after the batch."""
        return inbox.archive(file, wiki)

    @mcp.tool(annotations=_WRITE)
    def inbox_reject(
        file: FileArg,
        reason: Annotated[str, Field(description="Why the file could not be ingested")],
        wiki: WikiArg = None,
    ) -> dict[str, Any]:
        """Move an unusable inbox file to inbox/_failed/ and record the reason in
        <file>.reason.md next to it."""
        return inbox.reject(file, reason, wiki)

    return mcp


def create_app(settings: Settings | None = None) -> Any:
    """ASGI app: MCP at /mcp plus a /healthz probe."""
    settings = settings or get_settings()
    mcp = build_server(settings)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)  # type: ignore[untyped-decorator]
    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    hosts = settings.allowed_host_list()
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection="*" not in hosts,
        allowed_hosts=[p for h in hosts for p in (h, f"{h}:*")],
        allowed_origins=[],
    )
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=security,
        # inbox_add content is JSON-encoded; leave headroom over max_bytes.
        max_request_body_size=settings.max_bytes * 2 + 64 * 1024,
        host=settings.host,
    )


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
