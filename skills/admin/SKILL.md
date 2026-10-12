---
name: admin
description: >
  Inspect and adjust wiki spaces and configuration over MCP — list
  wikis, switch the default wiki, read and set config values.
type: skill
status: active
last_updated: "2026-10-12"
when_to_use: >
  Listing registered wikis, changing the default wiki, or reading or
  changing wiki configuration (search defaults, ingest behaviour,
  status weights).
tags: [spaces, config, multi-wiki, admin]
owner: jguibert@gmail.com
compatibility: "llm-wiki >= 1.0.0"
---

# Admin

> Merged from upstream's `spaces` and `config` skills, keeping only the
> MCP parts, for remote MCP-only use — see [UPSTREAM.md](../UPSTREAM.md).

Wikis (spaces) and the server configuration are managed by the server
operator. Over MCP you can inspect them and make the changes below.
Ignore `path` values in responses — they are server paths.

## Spaces

A space is a registered wiki repository with a name (used in
`wiki://<name>/<slug>` URIs and the `wiki` parameter of every tool)
and an optional description. One space is the default.

```
wiki_spaces_list()                 # all wikis
wiki_spaces_list(name: "<name>")   # one wiki
wiki_info()                        # includes default_wiki and index health
wiki_spaces_set_default(name: "<name>")
```

Creating, registering and removing spaces (`wiki_spaces_create`,
`wiki_spaces_register`, `wiki_spaces_remove`) operate on server paths
and are done by the operator. Do not call them unless the user
explicitly asks and gives the server-side path (new wikis live under
`/wikis/<name>` in the standard deployment). Never pass
`delete: true` to `wiki_spaces_remove` unless the user explicitly asks
to delete the wiki's data.

## Configuration

Settings resolve in this order: tool parameter → per-wiki
`wiki.toml` → global `config.toml` → built-in default.

```
wiki_config(action: "list")                                  # resolved config, default wiki
wiki_config(action: "list", wiki: "<name>")
wiki_config(action: "get", key: "defaults.search_top_k")
wiki_config(action: "set", key: "ingest.auto_commit", value: "false")              # per-wiki
wiki_config(action: "set", key: "defaults.search_top_k", value: "15", global: true) # global
```

Global-only keys reject per-wiki writes with an error.

### Per-wiki settings (also valid globally)

| Key | Default | Description |
|-----|---------|-------------|
| `defaults.search_top_k` | `10` | Default result count for search |
| `defaults.search_excerpt` | `true` | Include excerpts in search results |
| `defaults.search_sections` | `false` | Include section pages in search |
| `defaults.page_mode` | `flat` | Default page creation mode: `flat` or `bundle` |
| `defaults.list_page_size` | `20` | Default page size for list |
| `read.no_frontmatter` | `false` | Strip frontmatter from read output |
| `ingest.auto_commit` | `true` | Commit to git after `wiki_ingest` |
| `validation.type_strictness` | `loose` | `strict`: unknown type is error; `loose`: warning |
| `graph.format` | `mermaid` | Graph output format: `mermaid`, `dot`, `json`, or `llms` |
| `graph.depth` | `3` | Default hop limit for graph traversal |
| `graph.structural_algorithms` | `true` | Enable diameter/radius/center in `wiki_stats` |
| `graph.min_nodes_for_communities` | `30` | Minimum pages before community detection runs |
| `search.status.<key>` | — | Search weight for a status value (float) |
| `index.tokenizer` | `en_stem` | Tokenizer for text fields |

### Search status weights

Higher weight = pages with that status rank higher:

```
wiki_config(action: "set", key: "search.status.active", value: "1.2")
wiki_config(action: "set", key: "search.status.draft", value: "0.3")
wiki_config(action: "set", key: "search.status.stub", value: "0.5")
```

Custom status values from your schemas work the same way. Per-wiki
values override global ones.

`search.status.archived` and `search.status.superseded` are set by the
deployment (default `0.0`, env `LLM_WIKI_SEARCH_ARCHIVED` /
`LLM_WIKI_SEARCH_SUPERSEDED`) and are reapplied on restart. Leave them:
at `0.0`, retired pages score 0 and rank after every live hit. They are
still returned.

### Server settings — do not change over MCP

`serve.*`, `logging.*` and `watch.*` are owned by the deployment
(container entrypoint) and are reset on restart. Changing them over MCP
can break connectivity; ask the operator instead.
