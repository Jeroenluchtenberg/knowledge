# Decisions

ADR-style log. Each **VERIFY** item from the implementation plan is
recorded with what was checked and the outcome. Sources: llm-wiki at tag
`v1.0.1` (`cec948f`), llm-wiki-skills at `366e150`, agentgateway at tag
`v1.6.0`, mcp Python SDK 2.3.0. Verified on 2026-10-12.

---

## VERIFY results

### V1 — llm-wiki has no health endpoint → TCP healthcheck

- **Checked:** `src/server.rs` builds an axum router with only
  `nest_service("/mcp", …)`. No `/health` route exists.
- **Decision:** Docker `HEALTHCHECK` opens a TCP connection to the port
  (`bash -c 'exec 3<>/dev/tcp/127.0.0.1/8080'`). Compose
  `depends_on: condition: service_healthy` works with it (verified).

### V2 — `serve.http_allowed_hosts` value format and port matching

- **Checked:** `config.rs` `set_global_config_value`:
  `"serve.http_allowed_hosts" => value.split(',').map(trim)` — the CLI
  expects a **comma-separated string**, not a JSON list. The TOML stores
  it as an array.
- **Port:** not part of the match. Verified against the running
  container: `Host: llm-wiki:8080` → 200, `Host: 127.0.0.1:9999` → 200,
  `Host: evil.example` and `evil.example:8080` → 403.
- **Decision:** entrypoint runs
  `llm-wiki config set serve.http_allowed_hosts "$LLM_WIKI_ALLOWED_HOSTS" --global`
  on every start (default `localhost,127.0.0.1,llm-wiki`; compose adds
  `agentgateway`).

### V3 — How the engine sets the commit author

- **Checked:** `git.rs` `make_signature`: `repo.signature()` (libgit2
  reads `user.name`/`user.email` from repo → global `~/.gitconfig` →
  system config), falling back to `llm-wiki <llm-wiki@localhost>`.
- **Decision:** entrypoint writes `git config --global user.name/email`
  from `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`. Verified: commits show the
  configured author. Also sets `safe.directory='*'` for volumes owned by
  another UID.

### V4 — Does `wiki_content_new` return the scaffold?

- **Checked:** `handle_content_new` returns only
  `{"uri", "slug", "bundle"}` (the local `path`/`wiki_root` that the
  upstream skill documents are not returned over MCP).
- **Decision:** the `content` skill flow is
  `wiki_content_new` → `wiki_content_read` → `wiki_content_write` →
  `wiki_ingest`. The read is required.

### V5 — Does `wiki_content_commit` stage paths outside `wiki_root`?

- **Checked:** with no `slugs`, `content_commit(all=true)` calls
  `git::commit(repo_root)` → `index.add_all(["*"])` over the **whole
  repository**. Verified end-to-end (smoke test step 8): the commit
  shows `{inbox => raw}/note.md` and `inbox/{ => _failed}/junk.md`
  renames, and the old `inbox/` paths are no longer tracked —
  deletions are staged too.
- **Also found:** `wiki_ingest` with `ingest.auto_commit = true` uses the
  same `git::commit`, so **any ingest commits every pending change in
  the repo**, including files still waiting in `inbox/` (they enter git
  history before being processed). Accepted: history keeps the original
  source; the final `wiki_content_commit` records the move.
- **Consequence:** no upstream PR needed for raw/ coverage. Syncthing
  internals must be git-ignored → the entrypoint adds `.stfolder`,
  `.stversions/`, `.stignore`, `.syncthing.*.tmp`, `~syncthing~*` to the
  bootstrap wiki's `.gitignore` (idempotent).

### V6 — agentgateway config schema and multiplexing (v1.6.0)

- **Checked:** `schema/config.json` (`LocalMcpBackend`, `McpPrefixMode`)
  and `examples/mcp-multiplex/config.yaml` at tag `v1.6.0`.
- **Schema used:** `binds[].listeners[].routes[]` with
  `matches: [{path: {pathPrefix: /mcp}}]` and
  `backends: [{mcp: {prefixMode, targets: [{name, mcp: {host: <url>}}]}}]`.
  A remote Streamable HTTP target is `mcp: {host: "http://llm-wiki:8080/mcp"}`.
- **Multiplexing:** supported — multiple `targets` under one `mcp`
  backend. Default naming prefixes tools with `<target>_`; we set
  **`prefixMode: never`** because `wiki_*` and `inbox_*` never collide.
  Agents use the same tool names direct or via the gateway, so skills
  need no variants. Verified: `tools/list` through `:3000/mcp` returns
  all 25 `wiki_*` + 6 `inbox_*` tools, and the smoke test passes with
  `--gateway`.
- **Image:** `ghcr.io/agentgateway/agentgateway:v1.6.0` pinned by
  digest; runs as UID 65532; config via `-f /config/config.yaml`.

---

## Other decisions and findings

### D1 — mcp Python SDK 2.x (`MCPServer`, not `FastMCP`)

`mcp==2.3.0` renamed FastMCP to `mcp.server.mcpserver.MCPServer`;
`ToolAnnotations` uses snake_case fields. wiki-inbox is written against
2.3.0 (pinned). Tool modules must not use
`from __future__ import annotations` (the SDK evaluates annotations of
closures at registration time).

### D2 — Protocol 2026-07-28 interop gap (rmcp ↔ mcp 2.x client)

The mcp 2.x client probes `server/discover` and adopts protocol
`2026-07-28` with llm-wiki 1.0.1 (rmcp 3.4), whose `tools/list` result
then lacks the `cacheScope`/`ttlMs` fields that version requires →
client-side validation error. The classic `initialize` handshake works.
The smoke test uses `Client(..., mode="legacy")`. Clients that connect
with the `initialize` handshake are unaffected; a client that adopts
2026-07-28 against llm-wiki may hit this. Candidate upstream issue for
llm-wiki (rmcp). wiki-inbox (mcp 2.3.0 server) works with both.

Harmless: the client logs `Session termination failed: 202` because
rmcp answers the session `DELETE` with 202.

### D3 — `wiki_ingest` path for flat pages is `<slug>.md`

`wiki_ingest(path)` joins `path` to `wiki_root` and requires it to exist
(`ingest.rs`). A bare slug works only for bundles/sections (folders); a
flat page needs `<slug>.md`. The plan's `wiki_ingest(path: "<slug>")`
fails for flat pages; skills use `<slug>.md`.

### D4 — `wiki_spaces_list` has no `default` flag

The upstream `spaces` skill documents a `default` field; 1.0.1 returns
only `name`/`path`/`description`. Use `wiki_info()` (`default_wiki`).

### D5 — `git` is a runtime dependency of llm-wiki, not just debugging

`wiki_history` shells out to `git log --follow` (`git.rs` `page_history`).
The runtime image installs `git`.

### D6 — Container user and HOME

Compose overrides the UID (`user: "${UID}:${GID}"`), which may differ from
the build-time `ARG UID`. `/home/wiki` is mode `1777` so the engine can
write `~/.llm-wiki/` and `~/.gitconfig` as any UID. Both images still
create a `wiki` user with the build-arg UID/GID (default 1000:1000).

### D7 — Graceful shutdown

The engine only handles Ctrl-C (`tokio::signal::ctrl_c`), so the image
sets `STOPSIGNAL SIGINT`; `tini` is PID 1 and forwards it.

### D8 — Base images and pinning

| What | Pin |
|---|---|
| Rust builder | `rust:1.95-slim-bookworm@sha256:d748…` (matches upstream `rust-toolchain.toml` 1.95) |
| llm-wiki runtime | `debian:bookworm-slim@sha256:7c7b…` (same glibc as builder) |
| llm-wiki engine | `cargo install llm-wiki-engine --version 1.0.1 --locked` |
| wiki-inbox | `python:3.12-slim-bookworm@sha256:3438…`, `uv` 0.12.18 by digest, `uv.lock` |
| agentgateway | `v1.6.0@sha256:9d3e…` |

Builder needs only `build-essential` + `pkg-config` (libgit2 and zlib are
vendored).

### D9 — Image sizes

llm-wiki: ~300 MB (25 MB binary; git + perl ≈ 75 MB). wiki-inbox:
~760 MB, dominated by markitdown's dependencies (magika → onnxruntime →
sympy, pandas for XLSX, pdfminer, numpy). Acceptable for now; trimming
would mean replacing markitdown or installing it without its core
`magika` dependency.

### D10 — wiki-inbox transport: stateless + JSON responses

wiki-inbox keeps no per-session state, so it runs Streamable HTTP with
`stateless_http=True, json_response=True`. Survives restarts without
breaking client sessions; works behind the gateway. Host allow-list:
`INBOX_ALLOWED_HOSTS` (any port), mirroring llm-wiki. `/healthz` for the
Docker healthcheck. Request body limit is `2 × INBOX_MAX_BYTES + 64 KiB`.

### D11 — SSRF guard pins the resolved IP

`inbox_add_url` resolves the host once, rejects any non-global address
(private, loopback, link-local, CGNAT, multicast, reserved, IPv4-mapped
/ 6to4 / Teredo IPv6) — all resolved addresses must pass — and then
connects **to that IP** with the original `Host` header and TLS SNI
(`sni_hostname`), so DNS rebinding between check and connect is not
possible. Redirects are followed manually (max 5), each hop re-checked.
URLs with credentials are rejected. Size cap is enforced on
`Content-Length` and while streaming.

### D12 — Inbox file conventions

- `inbox_add` writes via temp file + hard link (atomic, fails instead of
  overwriting), so Syncthing never sees partial files and a collision
  becomes `-1`, `-2`, … .
- `inbox_add_url`: HTML is converted to `.md` with a two-line quote
  header (`> Source: <url>` / `> Fetched: <time>`) — no YAML front
  matter, since inbox files are sources, not pages. `text/plain` and
  `text/markdown` get the same header. Everything else is stored as-is
  with `<name>.source.json`.
- Sidecars move with their file on archive/reject and are hidden from
  `inbox_list` (`has_source: true` instead).
- Emptied inbox subfolders are removed after archive/reject.
- Unknown extensions are sniffed: UTF-8 without NUL bytes is text.

### D15 — Image publishing (GHCR)

The CI `publish` job runs on pushes to `main` only, after `wiki-inbox`,
`skills` and `images` (build + smoke test) pass. It pushes
`ghcr.io/<owner>/knowledge/llm-wiki` and `…/wiki-inbox` using the
workflow's `GITHUB_TOKEN` (`packages: write`), so no extra secret is needed.
The GHCR names are lowercased by `docker/metadata-action`.

- **Tags:** the component version (`LLM_WIKI_VERSION` from the
  Dockerfile = engine version, `version` from `wiki-inbox/pyproject.toml`),
  `sha-<short>` and `latest`. The version tag **moves** when the image
  changes without a version bump (e.g. entrypoint edits). Production
  pins `sha-<short>` or the digest.
- **Platform:** `linux/amd64` only. An arm64 build of the Rust engine
  under QEMU takes too long. Add a native arm64 runner if needed.
- **Build args:** default `UID`/`GID` 1000. Compose overrides the runtime
  user, so the published images work for any UID (D6).
- Reuses the `images` job's GHA build cache.
- New GHCR packages start **private**. Make them public once in the
  package settings if they should be pullable without login.

### D13 — Local port clash during verification

On the verification host port 8080 was taken by another service, so the
local `compose/.env` used `LLM_WIKI_PORT=18080`. The default stays 8080;
`scripts/smoke_test.py` reads the ports from `compose/.env`.
