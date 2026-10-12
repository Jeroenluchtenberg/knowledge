# CLAUDE.md

Conventions for agents working **on this repository**. Architecture:
[docs/architecture.md](docs/architecture.md). Decisions and verified
assumptions: [docs/decisions.md](docs/decisions.md).

## Rules

- **Agents use MCP only.** Wiki-using agents never touch the wiki
  filesystem: all reads/writes go through `wiki_*` (llm-wiki) and
  `inbox_*` (wiki-inbox). Skills in `skills/` must not instruct local
  Read/Write/Edit/Bash, `mv`, `wiki_resolve`, or `llm-wiki` CLI calls —
  the only exception is the `bootstrap` sentence that forbids them. CI
  greps for this.
- **Only llm-wiki commits.** Never add git operations to wiki-inbox.
- **Wiki names, never paths.** wiki-inbox tools accept a wiki *name*;
  every file argument is relative to that wiki's `inbox/` and checked
  with `paths.resolve_inside`.
- **Pin versions.** Base images by tag + digest, the engine by
  `--version … --locked`, Python deps exactly in `pyproject.toml` +
  `uv.lock`, agentgateway by tag + digest. Bump pins deliberately and
  note why in `docs/decisions.md`.
- **Non-root containers.** Configurable `UID`/`GID` (default 1000:1000),
  shared by both services and the host user; HOME must stay writable for
  any UID.
- **Record VERIFY results.** Any assumption about upstream behaviour
  (llm-wiki, agentgateway, MCP SDK) gets checked against the source or a
  running container and written down in `docs/decisions.md`.
- Don't guess agentgateway config — check the schema/examples for the
  pinned version.

## Layout

| Path | Notes |
|---|---|
| `llm-wiki/` | Dockerfile, `entrypoint.sh` (idempotent; renders config, applies env, bootstraps a wiki), config template |
| `wiki-inbox/src/wiki_inbox/` | `server.py` (tools; `Inbox` class is transport-independent), `config.py`, `paths.py`, `convert.py`, `fetch.py` |
| `skills/` | Vendored + modified skills; every change is listed in `skills/UPSTREAM.md` |
| `compose/` | Test stack; `agentgateway/config.yaml` is test-only (no auth) |
| `scripts/smoke_test.py` | End-to-end test (PEP 723, `uv run`) |

## Workflow

```bash
cd wiki-inbox && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src
make up-gateway && make smoke && make smoke-gateway
```

- wiki-inbox targets the `mcp` 2.x SDK (`MCPServer`, snake_case
  `ToolAnnotations`); don't use `from __future__ import annotations` in
  `server.py`.
- Changing a skill: keep the "Modified for remote MCP-only use" note and
  update `skills/UPSTREAM.md`.
- Never commit to `main`; branch and open a PR.
