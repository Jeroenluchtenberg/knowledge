# Plan: `knowledge` — self-hosted LLM knowledge stack

> Hand this file to Claude Code in an empty `knowledge` git repo. Work through
> the phases in order. Each phase ends with acceptance criteria; don't start the
> next phase until they pass. Items marked **VERIFY** are assumptions from a
> source review — check them against the actual code/docs before relying on them,
> and record what you found in `docs/decisions.md`.

## 1. Context

A centralized knowledge base following Karpathy's LLM-wiki pattern, used by
agents on several machines (Hermes agent, Claude Code, claude.ai) over MCP only.

- **Engine:** [geronimo-iia/llm-wiki](https://github.com/geronimo-iia/llm-wiki)
  v1.0.1 — Rust, git-backed Markdown wiki, BM25 search (tantivy), typed graph,
  MCP over stdio + Streamable HTTP (endpoint `/mcp`, default port 8080).
- **Gap:** the engine has no MCP tools for reading raw inbox files or moving them
  to `raw/`. Upstream skills do this with local file tools and `mv`, which
  remote agents can't do. → we build a small Python MCP server, **wiki-inbox**.
- **Agents never touch the filesystem.** All reads/writes go through MCP.
- **Auth** is handled outside this repo by agentgateway + Authentik (deployed
  with Ansible). This repo only needs an optional, unauthenticated gateway for
  local testing.
- The human (Obsidian) gets the wiki via Syncthing — out of scope here, but the
  repo layout must stay compatible: standard layout `inbox/`, `raw/`, `wiki/`,
  `schemas/`, `wiki.toml` at the wiki repo root.

### Constraints

- Containers run as a **non-root** user (configurable UID/GID, default 1000:1000)
  so they can share the wiki volume with the host user.
- Pin versions: llm-wiki engine, Python deps, base images, agentgateway.
- No git operations in wiki-inbox. Only llm-wiki commits (avoids
  `.git/index.lock` races between two processes).
- Single user; atomic writes / optimistic concurrency are explicitly **out of
  scope** (possible future upstream PR — see §9).

## 2. Target repo layout

```
knowledge/
├── README.md               # what this is, quick start, architecture diagram
├── CLAUDE.md               # conventions for agents working on this repo
├── docs/
│   ├── architecture.md     # components, data flow, ingestion flow
│   └── decisions.md        # ADR-style log, incl. results of every VERIFY item
├── llm-wiki/               # container image for the upstream engine
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── config.toml.template
├── wiki-inbox/             # Python ingestion MCP server
│   ├── pyproject.toml      # uv-managed
│   ├── uv.lock
│   ├── Dockerfile
│   ├── src/wiki_inbox/
│   │   ├── __init__.py
│   │   ├── server.py       # FastMCP app + tool definitions
│   │   ├── config.py       # settings + wiki-name → path resolution
│   │   ├── paths.py        # safe path handling
│   │   ├── convert.py      # file → markdown conversion
│   │   └── fetch.py        # URL fetching with SSRF guard
│   └── tests/
├── skills/                 # agent skills (vendored from upstream + modified)
│   ├── .claude-plugin/plugin.json
│   ├── UPSTREAM.md         # source repo, pinned commit, list of changes
│   ├── LICENSE-MIT
│   ├── LICENSE-APACHE
│   └── <skill>/SKILL.md ...
├── compose/
│   ├── docker-compose.yml
│   ├── .env.example
│   └── agentgateway/config.yaml   # test-only, no auth
├── scripts/
│   └── smoke_test.py       # end-to-end test against the compose stack
└── .github/workflows/ci.yml  # lint + unit tests + image builds (optional)
```

## 3. Phase 1 — repo skeleton

- `git init`, `.gitignore` (Python, `compose/data/`, `.env`), `README.md` stub,
  `CLAUDE.md` with: "agents use MCP only", "pin versions", "non-root
  containers", "record VERIFY results in docs/decisions.md".
- `docs/architecture.md`: diagram of agents → (agentgateway) → llm-wiki +
  wiki-inbox → shared wiki volume; and the ingestion sequence from §6.

**Accept:** repo builds nothing yet; docs describe the target.

## 4. Phase 2 — `llm-wiki/` container image

### Dockerfile (multi-stage)

- **Builder:** `rust:1.95-slim` (matches upstream `rust-toolchain.toml`).
  `cargo install llm-wiki-engine --version 1.0.1 --locked`.
  libgit2 is vendored (`git2` with `vendored-libgit2`), so only build essentials
  + pkg-config are needed; add whatever the build actually asks for.
- **Runtime:** `debian:bookworm-slim` (or `trixie-slim`), install
  `ca-certificates`, `git` (handy for debugging, `git log` in smoke tests),
  `tini`.
- Non-root user via `ARG UID=1000 GID=1000`; `HOME=/home/wiki` must be writable
  (engine writes `~/.llm-wiki/` snapshots and logs).
- `ENV LLM_WIKI_CONFIG=/config/config.toml`
- Volumes: `/wikis` (wiki repos), `/config` (global config).
- `EXPOSE 8080`, `ENTRYPOINT ["tini","--","/entrypoint.sh"]`.
- Healthcheck: TCP check on 8080 (no health endpoint upstream; **VERIFY**).

### entrypoint.sh

1. If `/config/config.toml` is missing, render it from `config.toml.template`.
2. Apply env-driven settings via the CLI (idempotent):
   - `llm-wiki config set serve.http_allowed_hosts '<json list>' --global` from
     `LLM_WIKI_ALLOWED_HOSTS` (default: `localhost,127.0.0.1,llm-wiki`).
     The server binds `0.0.0.0` but rejects other Host headers — the compose
     service name and the gateway hostname must be in this list.
     **VERIFY** the value format the CLI expects and whether the port is part of
     the match.
   - `logging.log_format json`, `logging.log_path ""` (stderr only, for Docker).
3. Git identity for engine commits: `git config --global user.name/user.email`
   from `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL`. **VERIFY** how the engine sets
   the commit author (git2 may read repo/global config or use its own default).
4. Optional bootstrap: if `LLM_WIKI_BOOTSTRAP_WIKI=<name>` is set and no wiki is
   registered, run `llm-wiki spaces create /wikis/<name> --name <name>` and
   `spaces set-default <name>`.
5. `exec llm-wiki serve --http :8080` (add `--watch` only if
   `LLM_WIKI_WATCH=true`; not needed when all writes go through MCP).

**Accept:**
- `docker build` succeeds; image runs as UID 1000; image size reasonable.
- `curl` to `http://localhost:8080/mcp` with a valid MCP `initialize` request
  returns a session; `tools/list` shows the `wiki_*` tools.
- A request with an unlisted Host header is rejected (proves the allow-list
  works).

## 5. Phase 3 — `wiki-inbox/` Python MCP server

### Stack

Python 3.12, `uv`, official `mcp` SDK (FastMCP) with **Streamable HTTP**
transport on port 8081, path `/mcp`. Conversion: `markitdown` (PDF, DOCX,
PPTX, XLSX, HTML → Markdown). Fetching: `httpx`. Settings: `pydantic-settings`.
Tests: `pytest`. Lint: `ruff`, types: `mypy` or `pyright`.

### Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `INBOX_LLM_WIKI_CONFIG` | `/config/config.toml` | Read **read-only** to map wiki name → repo path (`[[wikis]] name/path`, `[global] default_wiki`). Re-read on each call (cheap) so new spaces appear without restart. |
| `INBOX_WIKIS` | – | Optional fallback/override: `name=/path,name2=/path2` |
| `INBOX_MAX_BYTES` | 25 MB | Max file size for add/fetch/read |
| `INBOX_READ_CHUNK` | 40000 chars | Default `limit` for `inbox_read` |
| `INBOX_READ_ONLY` | false | When true, only `inbox_list`/`inbox_read` are registered |
| `INBOX_FETCH_ALLOW_PRIVATE` | false | SSRF guard for `inbox_add_url` |

### Tools

All tools take an optional `wiki` (defaults to `default_wiki`). Paths are
always relative to that wiki's `inbox/`.

| Tool | Behaviour |
|---|---|
| `inbox_list(wiki?)` | Files in `inbox/` (recursive), excluding `_failed/`, dotfiles and Syncthing temp/conflict files (`.syncthing.*.tmp`, `~syncthing~*`, `*.sync-conflict-*` → list separately as `conflicts`). Returns name, size, mtime, detected type, `convertible: bool`. Sorted oldest first. |
| `inbox_read(file, wiki?, offset=0, limit=?)` | Returns Markdown text. Text formats returned as-is; binary formats converted via markitdown. Cache conversions keyed by (path, mtime, size) under `/tmp`. Returns `{content, offset, next_offset, total_chars, truncated}` so agents can page large docs. Unconvertible → clear error suggesting `inbox_reject`. |
| `inbox_add(filename, content, wiki?)` | Writes a UTF-8 text/Markdown file into `inbox/`. Sanitize filename (no separators, no leading dot, safe charset, `.md` default extension). Never overwrite: on collision append `-1`, `-2`, … Returns final name. |
| `inbox_add_url(url, wiki?, filename?)` | Server-side fetch (timeout, size cap, `http/https` only, block private/loopback/link-local IPs after DNS resolution unless allowed). Store HTML as `.html` (or converted `.md`), other types with proper extension. Write a small front-matter-free header noting source URL + fetch time at the top of `.md` outputs, or a sidecar `<name>.source.json` for binaries. |
| `inbox_archive(file, wiki?)` | Move `inbox/<file>` → `raw/<file>` (create dirs, keep subfolders, suffix on collision; move sidecar too). Returns new path relative to repo root. Does **not** commit. |
| `inbox_reject(file, reason, wiki?)` | Move to `inbox/_failed/<file>` and write `<file>.reason.md` with the reason and timestamp. |

### Safety rules (`paths.py`)

- Resolve every path with `Path.resolve()` and require it to be inside the
  wiki's `inbox/` (or `raw/` for archive targets). Reject symlinks pointing
  outside. Reject absolute paths and `..` in input.
- Wiki name must exist in config; never accept a raw filesystem path from the
  agent.
- Enforce `INBOX_MAX_BYTES` on read, add and fetch (stream + abort).

### Container

- `python:3.12-slim` base, `uv sync --frozen --no-dev`, non-root user with the
  same `ARG UID/GID` as llm-wiki.
- Mount `/wikis` read-write, `/config` **read-only**.
- `EXPOSE 8081`.

### Tests

Unit tests for: path traversal attempts (`../`, absolute, symlink escape),
filename sanitizing and collision suffixes, Syncthing file filtering, paging
math in `inbox_read`, conversion of a sample PDF/DOCX/HTML fixture, archive
collision handling, reject sidecar, read-only mode hiding mutating tools, SSRF
guard (mock DNS → 127.0.0.1 / 10.x / 169.254.x are blocked), config parsing
with an example llm-wiki `config.toml`.

**Accept:** `uv run pytest` green, `ruff` clean, image builds, `tools/list`
over HTTP shows the six tools (two in read-only mode).

## 6. Phase 4 — `skills/`

### Source

Vendor [geronimo-iia/llm-wiki-skills](https://github.com/geronimo-iia/llm-wiki-skills)
at a pinned commit (record the SHA in `skills/UPSTREAM.md`). Keep both licenses
(MIT OR Apache-2.0) and attribution. Every modified skill gets a short
"Modified for remote MCP-only use — see UPSTREAM.md" note in its body.

### Plugin manifest

`skills/.claude-plugin/plugin.json` with name `knowledge` (not `llm-wiki`, to
avoid clashing with the upstream plugin). No `.mcp.json` that starts a local
`llm-wiki serve` — agents connect to the remote servers.

### Which skills

| Skill | Action |
|---|---|
| `bootstrap` | Keep, **modify**: add a "Remote mode" section — no filesystem access to the wiki; all operations via `wiki_*` and `inbox_*` MCP tools; never use local Read/Write/Edit/Bash on wiki paths, and ignore any local `path` values returned by tools. |
| `content` | **Modify** (see below) |
| `ingest` | **Rewrite** around wiki-inbox (see below) |
| `capture` | **New**: queue a source for later ingestion — `inbox_add` for text/notes, `inbox_add_url` for links. Short, model-invocable ("save this for the wiki"). |
| `crystallize`, `research`, `review`, `lint`, `graph`, `stats`, `export`, `frontmatter` (+ `references/`), `schema` | Keep; audit (see below) |
| `spaces`, `config` | Keep only the MCP parts (`wiki_spaces_*`, `wiki_config`); remove CLI-only instructions. If little remains, merge into one `admin` skill. |
| `setup`, `migrate`, `configure-hugo` | **Drop** (local install / filesystem admin; done on the server by hand). List them in `UPSTREAM.md` as removed. |

### `content` modifications

- Remove the "write directly to the returned path with Edit/Write" workflow and
  every reference to `wiki_resolve` for obtaining file paths.
- Create flow: `wiki_content_new(uri, type)` → `wiki_content_read(uri)` to get
  the scaffolded frontmatter/body → `wiki_content_write(uri, content)` with the
  full page → `wiki_ingest(path: "<slug>")` to validate, index and commit.
  **VERIFY** whether `wiki_content_new` already returns the scaffold content;
  if so, skip the read.
- Edit flow: `wiki_content_read` → `wiki_content_write` with the full updated
  page. Add guidance: change only what's needed, preserve all other text and
  frontmatter verbatim, re-read before writing if time has passed.

### `ingest` rewrite

Keep upstream's synthesis guidance (classify, search existing knowledge,
update vs create, frontmatter, cross-linking, confidence), replace the
file-handling steps:

1. `inbox_list` → if empty, report and stop. Mention any `conflicts`.
2. For each file, oldest first, one at a time:
   1. `inbox_read` (page through with `next_offset` until done).
   2. Search/read existing pages (`wiki_search`, `wiki_content_read`,
      `wiki_suggest`).
   3. Create/update pages via the modified `content` flow.
   4. `wiki_ingest` on the touched slugs.
   5. On success → `inbox_archive(file)`. On failure or unusable source →
      `inbox_reject(file, reason)`.
3. After the batch: `wiki_content_commit` (all) so the `inbox/` → `raw/` moves
   are committed. **VERIFY** that `wiki_content_commit` stages paths outside
   `wiki_root` (`raw/`, `inbox/`). If it doesn't, document the gap in
   `docs/decisions.md` and note it as a candidate upstream PR — do **not** add
   git operations to wiki-inbox.
4. Report: processed / archived / rejected (with reasons) / pages created and
   updated.
- Remove the upstream "skip binary files" list — wiki-inbox converts them;
  reject only when `inbox_read` fails.
- Remove the contradiction about who moves files (engine vs agent): wiki-inbox
  does it via `inbox_archive`.

### Audit of all kept skills

`grep` every SKILL.md for local-filesystem or CLI assumptions — `Read`,
`Write`, `Edit`, `Bash`, `mv`, `rm`, `ls`, `cat`, "local path", `wiki_resolve`,
`llm-wiki ` CLI invocations — and convert each to the MCP equivalent or delete
it. Also check frontmatter compatibility with Hermes (SKILL.md / agentskills
format: `name` + `description` required); keep extra fields only if harmless.
Record each change in `UPSTREAM.md`.

**Accept:** no remaining references to local file tools or CLI in `skills/`
(except a `bootstrap` sentence forbidding them); `UPSTREAM.md` lists every
added, removed and modified skill.

## 7. Phase 5 — `compose/` test stack

### docker-compose.yml

- `llm-wiki`: built from `../llm-wiki`; volumes `./data/wikis:/wikis`,
  `./data/config:/config`; env `LLM_WIKI_BOOTSTRAP_WIKI=main`,
  `LLM_WIKI_ALLOWED_HOSTS=localhost,127.0.0.1,llm-wiki,agentgateway`,
  `GIT_AUTHOR_*`; port `127.0.0.1:8080:8080`.
- `wiki-inbox`: built from `../wiki-inbox`; volumes `./data/wikis:/wikis`,
  `./data/config:/config:ro`; `depends_on: llm-wiki (healthy)`; port
  `127.0.0.1:8081:8081`.
- `agentgateway` (compose **profile `gateway`**, optional): pinned image,
  `./agentgateway/config.yaml`, port `127.0.0.1:3000`. Test-only config with no
  auth: one listener, MCP route to both targets (`llm-wiki` → `http://llm-wiki:8080/mcp`,
  `wiki-inbox` → `http://wiki-inbox:8081/mcp`), multiplexed into one endpoint
  if supported. **VERIFY** the config schema and multiplexing syntax against
  the agentgateway docs for the pinned version; don't guess. The production
  config (Authentik, routes.d fragments) lives in the Ansible roles, not here.
- Both app services: `user: "${UID:-1000}:${GID:-1000}"`, shared network
  `knowledge`.
- `.env.example` with UID/GID, git author, image tags.
- `./data/` is git-ignored; a `make up` / `scripts/` helper creates it with the
  right ownership.

### Example client config

Add a `compose/mcp.example.json` showing how Claude Code / Hermes connect to
both servers (direct ports and via gateway).

**Accept:** `docker compose up -d` brings both services healthy;
`docker compose --profile gateway up -d` additionally exposes one combined MCP
endpoint.

## 8. Phase 6 — end-to-end smoke test

`scripts/smoke_test.py` (uses the `mcp` Python client, runnable via
`uv run`), against direct ports by default, `--gateway` to go through
agentgateway:

1. `tools/list` on both servers; assert expected tools.
2. `wiki_spaces_list` shows `main` as default.
3. `inbox_add("smoke-note.md", ...)` and `inbox_add_url` against a local fixture
   (or skip if offline); drop a small PDF fixture into `data/wikis/main/inbox/`
   to simulate Syncthing.
4. `inbox_list` sees all three; `inbox_read` returns Markdown for the PDF.
5. Create a page: `wiki_content_new` → `wiki_content_write` → `wiki_ingest`.
6. `wiki_search` finds it.
7. `inbox_archive` one file, `inbox_reject` another; `wiki_content_commit`.
8. `git -C data/wikis/main log --stat -n 3` shows the page commit and (if
   supported) the `raw/` move.
9. Path traversal attempt (`../wiki.toml`) is rejected.

**Accept:** script exits 0 on a fresh stack; README documents how to run it.

## 9. Out of scope / future

- Ansible roles (llm-wiki, wiki-inbox, agentgateway with `routes.d` fragments,
  one Authentik application for all MCP servers).
- Syncthing setup for Obsidian (`wiki/` Receive Only, `inbox/` two-way).
- Read-only access route: filter mutating tools at agentgateway, plus
  `INBOX_READ_ONLY=true` on a second wiki-inbox instance if needed.
- Candidate upstream PRs to llm-wiki: `wiki_content_patch` (old_str/new_str),
  `expected_version` on `wiki_content_write`, atomic writes (temp file +
  rename), commit coverage for `raw/` if missing.

## 10. Definition of done

- All phase acceptance criteria pass.
- `docs/decisions.md` records the outcome of every **VERIFY** item.
- README quick start: clone → `cp compose/.env.example compose/.env` →
  `docker compose up -d` → run smoke test → connect an agent.
