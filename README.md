# knowledge

Self-hosted LLM knowledge stack following Karpathy's LLM-wiki pattern:
sources land in an inbox, agents synthesize them into a git-backed
Markdown wiki, and every agent — Hermes, Claude Code, claude.ai — on any
machine works with it **over MCP only**.

```
agents ──MCP──► agentgateway ──► llm-wiki   (wiki_*  : search, graph, pages, git)
                             └─► wiki-inbox (inbox_* : read/convert, capture, archive)
                                      │
                       shared git repo /wikis/<name>/{inbox,raw,wiki,schemas}
                                      │
                               Syncthing ⇄ Obsidian (human)
```

| Directory | What |
|---|---|
| `llm-wiki/` | Container image for the upstream [llm-wiki](https://github.com/geronimo-iia/llm-wiki) engine 1.0.1 |
| `wiki-inbox/` | Python MCP server for the ingestion queue (list/read with PDF·DOCX·HTML → Markdown, add, fetch URL, archive, reject) |
| `skills/` | Agent skills (Claude Code plugin `knowledge`), adapted from [llm-wiki-skills](https://github.com/geronimo-iia/llm-wiki-skills) for remote MCP-only use |
| `compose/` | Local test stack; optional unauthenticated agentgateway |
| `scripts/` | `smoke_test.py` (end-to-end), `init-data.sh` |
| `docs/` | [architecture](docs/architecture.md), [decisions](docs/decisions.md) (incl. all verified assumptions) |

Production auth (agentgateway + Authentik) and Syncthing are deployed
with Ansible, outside this repo.

## Quick start

Requires Docker with Compose v2 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Jeroenluchtenberg/knowledge.git && cd knowledge
cp compose/.env.example compose/.env      # set UID/GID to your user (id -u / id -g)
scripts/init-data.sh                      # creates compose/data/ with that owner
docker compose -f compose/docker-compose.yml up -d --build --wait
```

This builds both images (the first Rust build takes a few minutes) and
starts:

- **llm-wiki** on `http://localhost:8080/mcp` — creates and registers the
  wiki `main` (`compose/data/wikis/main`) on first start.
- **wiki-inbox** on `http://localhost:8081/mcp`.

Add the gateway (one combined endpoint at `http://localhost:3000/mcp`,
**no auth — local testing only**):

```bash
docker compose -f compose/docker-compose.yml --profile gateway up -d --wait
```

`make up`, `make up-gateway`, `make down`, `make logs` wrap the same
commands.

### Run the smoke test

```bash
uv run scripts/smoke_test.py             # direct ports
uv run scripts/smoke_test.py --gateway   # through agentgateway
```

It lists tools on both servers, queues a note, a URL
(`--url-fixture ''` to skip when offline) and a PDF dropped straight into
`inbox/` (simulating Syncthing), reads the PDF as Markdown, creates and
ingests a page, finds it with search, archives one source and rejects
another, commits, checks `git log`, and checks that path traversal is
rejected. Exit code 0 means everything works. Ports are read from
`compose/.env`.

### Connect an agent

Claude Code (`.mcp.json` in your project, or `claude mcp add`):

```json
{
  "mcpServers": {
    "knowledge": { "type": "http", "url": "http://localhost:3000/mcp" }
  }
}
```

or the two servers directly — see
[compose/mcp.example.json](compose/mcp.example.json). Tool names are the
same either way (the gateway does not prefix them).

Hermes (`~/.hermes/config.yaml`; check the key names against your
Hermes version):

```yaml
mcp_servers:
  knowledge:
    url: "http://localhost:3000/mcp"
```

Install the skills as a Claude Code plugin from `skills/` (plugin name
`knowledge`), or copy the `skills/<name>/` folders into your agent's
skills directory. Start a session with the **bootstrap** skill; use
**capture** to queue sources and **ingest** to process the inbox.

## Development

```bash
make test        # wiki-inbox unit tests
make lint        # ruff + mypy
make smoke       # end-to-end against the running stack
```

Conventions for agents working on this repo: [CLAUDE.md](CLAUDE.md).

## Licenses

`skills/` is derived from llm-wiki-skills (MIT OR Apache-2.0; see
`skills/UPSTREAM.md`, `skills/LICENSE-*`). The llm-wiki engine is
MIT OR Apache-2.0 and is built from crates.io, not vendored.
