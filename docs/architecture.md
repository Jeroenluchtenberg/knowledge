# Architecture

A centralized knowledge base following Karpathy's LLM-wiki pattern
(`raw/` sources → synthesized `wiki/` pages), used by agents on several
machines **over MCP only**.

## Components

```
 Agents (Hermes, Claude Code, claude.ai, …)          Human
        │  MCP (Streamable HTTP)                        │ Obsidian
        ▼                                               ▼
 ┌─────────────────────────────────────┐          Syncthing
 │ agentgateway                        │      (wiki/ receive-only,
 │  prod: Authentik auth (Ansible)     │       inbox/ two-way)
 │  test: compose profile `gateway`,   │               │
 │        no auth, :3000/mcp           │               │
 │  multiplexes both targets into one  │               │
 │  MCP endpoint (tool names unchanged)│               │
 └──────────┬───────────────┬──────────┘               │
            │               │                          │
            ▼               ▼                          │
 ┌──────────────────┐ ┌──────────────────┐             │
 │ llm-wiki :8080   │ │ wiki-inbox :8081 │             │
 │ (Rust, upstream) │ │ (Python, here)   │             │
 │ wiki_* tools     │ │ inbox_* tools    │             │
 │ search, graph,   │ │ list/read/add/   │             │
 │ validate, COMMIT │ │ fetch/archive/   │             │
 │                  │ │ reject — NO git  │             │
 └───────┬──────────┘ └───────┬──────────┘             │
         │ rw                 │ rw (config: ro)        │
         ▼                    ▼                        ▼
 ┌────────────────────────────────────────────────────────────┐
 │ shared volume /wikis/<name>/   (git repo, UID:GID shared)  │
 │   inbox/   queue (Syncthing drops, inbox_add, inbox_add_url)│
 │   inbox/_failed/   rejected sources + <file>.reason.md      │
 │   raw/     archived sources (record of completion)          │
 │   wiki/    pages (Markdown + YAML frontmatter)              │
 │   schemas/ type schemas      wiki.toml   per-wiki config    │
 │ /config/config.toml  llm-wiki global config (wiki registry) │
 └────────────────────────────────────────────────────────────┘
```

| Component | Source | Role |
|---|---|---|
| **llm-wiki** | [geronimo-iia/llm-wiki](https://github.com/geronimo-iia/llm-wiki) 1.0.1, built in `llm-wiki/` | Git-backed wiki engine: BM25 search (tantivy), typed graph, frontmatter validation, git commits. MCP over Streamable HTTP at `/mcp`. |
| **wiki-inbox** | `wiki-inbox/` (this repo) | Fills the engine's gap: MCP tools to read inbox files (with conversion to Markdown), queue notes/URLs, and move files to `raw/` or `inbox/_failed/`. Never runs git. |
| **agentgateway** | upstream image v1.6.0 | Single MCP endpoint for agents; auth in production. The test config in `compose/agentgateway/` has no auth. |
| **skills** | `skills/` (from geronimo-iia/llm-wiki-skills) | Agent workflows (ingest, capture, content, …) rewritten for MCP-only use. |

### Invariants

- **Agents never touch the filesystem.** Everything goes through `wiki_*`
  and `inbox_*` tools. Paths returned by the engine are server paths.
- **Only llm-wiki commits.** wiki-inbox moves files but never runs git,
  so there is one writer of `.git/` (no `index.lock` races).
- **Wiki names, never paths.** wiki-inbox resolves `wiki` by name from
  llm-wiki's `config.toml` (read-only mount, re-read per call).
- **Same UID/GID** for both containers and the host user (Syncthing), so
  files stay editable everywhere. Containers run non-root.
- `wiki_ingest` and `wiki_content_commit` (without slugs) stage the
  **whole repository** (`git add -A` semantics), including `inbox/`,
  `raw/` and deletions. The bootstrap wiki gets a `.gitignore` for
  Syncthing internals.

## Ingestion flow

```
 user / agent                       wiki-inbox            llm-wiki
 ─────────────                      ──────────            ────────
 capture: inbox_add(note)      ───► write inbox/note.md (atomic, never overwrite)
          inbox_add_url(url)   ───► fetch (SSRF guard) → inbox/<host>-<page>.md
                                    or <file>.pdf + <file>.pdf.source.json
 Syncthing drops paper.pdf     ───► (file appears in inbox/)

 ingest skill, per file, oldest first:
 1. inbox_list                 ───► files[] (+ conflicts[])
 2. inbox_read(file, offset)   ───► Markdown (markitdown, cached), paged
 3. wiki_search / wiki_content_read / wiki_suggest ───────────► existing knowledge
 4. wiki_content_new → wiki_content_read → wiki_content_write ─► wiki/<slug>.md
 5. wiki_ingest("<slug>.md")   ─────────────────────────────────► validate, index,
                                                                  commit (whole repo)
 6a. success: inbox_archive(file) ─► mv inbox/file → raw/file (+ sidecar)
 6b. failure: inbox_reject(file, reason) ─► mv → inbox/_failed/file
                                              + inbox/_failed/file.reason.md
 after the batch:
 7. wiki_content_commit()      ─────────────────────────────────► commit moves
 8. report: processed / archived / rejected / pages created & updated
```

## Ports and endpoints

| Service | Container port | Compose host port | Endpoint |
|---|---|---|---|
| llm-wiki | 8080 | `127.0.0.1:${LLM_WIKI_PORT:-8080}` | `/mcp` (no health endpoint; TCP healthcheck) |
| wiki-inbox | 8081 | `127.0.0.1:${WIKI_INBOX_PORT:-8081}` | `/mcp`, `/healthz` |
| agentgateway | 3000 | `127.0.0.1:${AGENTGATEWAY_PORT:-3000}` | `/mcp` (both servers multiplexed) |

Both servers validate the `Host` header against an allow-list (DNS
rebinding guard): `LLM_WIKI_ALLOWED_HOSTS` / `INBOX_ALLOWED_HOSTS`. The
compose service names and `agentgateway` are on the lists.

## Out of scope here

Ansible roles (production agentgateway with Authentik and `routes.d`
fragments), Syncthing setup, and a read-only access route
(`INBOX_READ_ONLY=true` + tool filtering at the gateway). See
[knowledge-PLAN.md](../knowledge-PLAN.md) §9.
