# wiki-inbox

MCP server (Streamable HTTP, `/mcp` on port 8081) for the llm-wiki
ingestion queue. See the [top-level README](../README.md) and
[docs/architecture.md](../docs/architecture.md).

| Tool | Does |
|---|---|
| `inbox_list(wiki?)` | Files in `inbox/`, oldest first; Syncthing conflicts listed separately |
| `inbox_read(file, wiki?, offset=0, limit?)` | Markdown (PDF/DOCX/PPTX/XLSX/HTML/EPUB converted), paged |
| `inbox_add(filename, content, wiki?)` | Queue a text/Markdown note (never overwrites) |
| `inbox_add_url(url, wiki?, filename?)` | Fetch a URL server-side (SSRF-guarded) |
| `inbox_archive(file, wiki?)` | Move to `raw/` (no commit) |
| `inbox_reject(file, reason, wiki?)` | Move to `inbox/_failed/` + `<file>.reason.md` |

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `INBOX_LLM_WIKI_CONFIG` | `/config/config.toml` | llm-wiki global config (read-only): wiki name → repo path, default wiki. Re-read per call. |
| `INBOX_WIKIS` | – | Override/fallback: `name=/path,name2=/path2` |
| `INBOX_MAX_BYTES` | `26214400` (25 MB) | Max size for add, fetch and read |
| `INBOX_READ_CHUNK` | `40000` | Default `limit` (characters) for `inbox_read` |
| `INBOX_READ_ONLY` | `false` | Register only `inbox_list` / `inbox_read` |
| `INBOX_FETCH_ALLOW_PRIVATE` | `false` | Allow `inbox_add_url` to reach private/loopback/link-local addresses |
| `INBOX_FETCH_TIMEOUT` | `30` | Seconds per fetch hop |
| `INBOX_ALLOWED_HOSTS` | `localhost,127.0.0.1,wiki-inbox` | Host header allow-list (any port); `*` disables |
| `INBOX_CACHE_DIR` | `/tmp/wiki-inbox-cache` | Conversion cache (keyed by path, mtime, size) |
| `INBOX_HOST` / `INBOX_PORT` | `0.0.0.0` / `8081` | Listen address |

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy src
INBOX_WIKIS=main=/path/to/wiki uv run wiki-inbox   # serve locally
```
